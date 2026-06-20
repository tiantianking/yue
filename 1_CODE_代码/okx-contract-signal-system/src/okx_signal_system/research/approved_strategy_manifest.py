from __future__ import annotations

import getpass
import json
import os
import threading
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from okx_signal_system.io_atomic import replace_with_retry
from okx_signal_system.strategy.trend_breakout import StrategyParams

APPROVED_MANIFEST_FILENAME = "approved_strategy_manifest.json"
CANDIDATE_PARAMS_FILENAME = "candidate_params.json"
RUNTIME_MANIFEST_DIRNAME = "runtime"
RESEARCH_RUNS_DIRNAME = "research_runs"
MANIFEST_SCHEMA_VERSION = 1
MANIFEST_TYPE = "approved_strategy_params"
STRICT_RESEARCH_CANDIDATE_TYPE = "strict_research_candidate"
APPROVED_STRATEGY_VERSION = "3.56.8"
APPROVED_RESEARCH_VERSION = "v3.56-strict"
BLIND_SEALED_PASS = "BLIND_SEALED_PASS"

PARAM_FIELDS = tuple(StrategyParams.__dataclass_fields__.keys())


class ManifestValidationError(ValueError):
    """Raised when an approved runtime manifest is missing or invalid."""


class CandidatePromotionError(ValueError):
    """Raised when a research candidate cannot be promoted."""


@dataclass(frozen=True)
class ApprovedManifestStatus:
    path: Path
    ok: bool
    reason: str
    params: StrategyParams
    manifest: dict[str, Any]

    @property
    def push_allowed(self) -> bool:
        return self.ok

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "ok": self.ok,
            "reason": self.reason,
            "push_allowed": self.push_allowed,
            "selected_params": asdict(self.params),
            "manifest_hash": str(self.manifest.get("manifest_hash", "")) if self.manifest else "",
            "manifest_sha256": str(self.manifest.get("manifest_sha256", "")) if self.manifest else "",
            "research_run_id": str(self.manifest.get("research_run_id", "")) if self.manifest else "",
            "candidate_generated_at": str(self.manifest.get("candidate_generated_at", "")) if self.manifest else "",
            "approved_at": str(self.manifest.get("approved_at", "")) if self.manifest else "",
            "promotion_approved_at": str(self.manifest.get("promotion_approved_at", "")) if self.manifest else "",
        }


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def canonical_sha256(value: Any) -> str:
    import hashlib

    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def file_sha256(path: str | Path) -> str:
    import hashlib

    target = Path(path)
    digest = hashlib.sha256()
    with target.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def approved_manifest_path(output_dir: str | Path | None = None) -> Path:
    from okx_signal_system.config import project_paths

    out = Path(output_dir) if output_dir is not None else project_paths().output_dir
    return out / RUNTIME_MANIFEST_DIRNAME / APPROVED_MANIFEST_FILENAME


def research_run_dir(output_dir: str | Path, run_id: str) -> Path:
    normalized = str(run_id).strip()
    if not normalized:
        raise CandidatePromotionError("RUN_ID_REQUIRED")
    if any(part in {"", ".", ".."} for part in Path(normalized).parts) or Path(normalized).is_absolute():
        raise CandidatePromotionError("RUN_ID_INVALID")
    return Path(output_dir) / RESEARCH_RUNS_DIRNAME / normalized


def candidate_params_path(output_dir: str | Path, run_id: str) -> Path:
    return research_run_dir(output_dir, run_id) / CANDIDATE_PARAMS_FILENAME


def strategy_params_from_dict(data: dict[str, Any]) -> StrategyParams:
    missing = [field for field in PARAM_FIELDS if field not in data]
    if missing:
        raise ManifestValidationError(f"runtime_manifest_params_missing:{','.join(missing)}")
    try:
        params = StrategyParams(
            fast_ema=int(data["fast_ema"]),
            slow_ema=int(data["slow_ema"]),
            breakout_window=int(data["breakout_window"]),
            atr_stop_mult=float(data["atr_stop_mult"]),
            take_profit_mult=float(data["take_profit_mult"]),
            max_hold_bars=int(data["max_hold_bars"]),
            atr_window=int(data["atr_window"]),
        )
    except (TypeError, ValueError) as exc:
        raise ManifestValidationError(f"runtime_manifest_params_invalid:{exc}") from exc
    if params.fast_ema <= 0 or params.slow_ema <= 0 or params.breakout_window <= 0:
        raise ManifestValidationError("runtime_manifest_params_invalid:window_must_be_positive")
    if params.atr_stop_mult <= 0 or params.take_profit_mult < 3.5:
        raise ManifestValidationError("runtime_manifest_params_invalid:risk_reward_floor")
    if params.max_hold_bars <= 0 or params.atr_window <= 0:
        raise ManifestValidationError("runtime_manifest_params_invalid:bars_must_be_positive")
    return params


def params_dict(params: StrategyParams | dict[str, Any]) -> dict[str, Any]:
    if isinstance(params, StrategyParams):
        return asdict(params)
    return asdict(strategy_params_from_dict(params))


def _utc_now_text() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        text = str(value).replace("Z", "+00:00")
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _normalise_blind_status(value: Any) -> str:
    return {
        "locked": "BLIND_LOCKED",
        "unlocked": "BLIND_OPENED",
        "sealed_pass": BLIND_SEALED_PASS,
        "sealed_fail": "BLIND_SEALED_FAIL",
    }.get(str(value), str(value))


def _candidate_blind_evidence(candidate: dict[str, Any]) -> tuple[str, bool]:
    metadata = candidate.get("research_metadata", {})
    if not isinstance(metadata, dict):
        metadata = {}
    evaluation = metadata.get("blind_evaluation", {})
    if not isinstance(evaluation, dict):
        evaluation = {}
    status = _normalise_blind_status(
        candidate.get("blind_status")
        or evaluation.get("status")
        or metadata.get("blind_lock_status", "")
    )
    return status, bool(evaluation.get("passed", False))


def _research_semantic_error(payload: dict[str, Any]) -> str | None:
    metadata = payload.get("research_metadata")
    if not isinstance(metadata, dict):
        return "research_metadata_missing"
    if str(payload.get("research_version", "")) != APPROVED_RESEARCH_VERSION:
        return "research_version_mismatch"
    if str(metadata.get("research_version", "")) != APPROVED_RESEARCH_VERSION:
        return "metadata_research_version_mismatch"
    if str(metadata.get("research_mode", "")) != "FORMAL":
        return "metadata_research_mode_invalid"
    if metadata.get("promotion_eligible") is not True:
        return "metadata_promotion_not_eligible"
    if metadata.get("blind_commitment_verified") is not True:
        return "blind_commitment_not_verified"
    for field in ("dataset", "signal_timeframe", "trend_timeframe"):
        if not str(payload.get(field, "")).strip():
            return f"{field}_missing"
        if str(metadata.get(field, "")) != str(payload.get(field, "")):
            return f"metadata_{field}_mismatch"
    coverage_pairs = (
        ("expected_parameter_combinations", "completed_parameter_combinations"),
        ("expected_parameter_cells", "completed_parameter_cells"),
    )
    for expected_field, completed_field in coverage_pairs:
        try:
            expected = int(metadata.get(expected_field, 0))
            completed = int(metadata.get(completed_field, 0))
        except (TypeError, ValueError):
            return "research_grid_coverage_invalid"
        if expected <= 0 or completed != expected:
            return "research_grid_coverage_incomplete"
    return None


def _manifest_hash_payload(manifest: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in manifest.items() if key not in {"manifest_hash", "manifest_sha256"}}


def _candidate_params(candidate: dict[str, Any]) -> dict[str, Any]:
    raw = candidate.get("candidate_params")
    if raw is None:
        raw = candidate.get("selected_params")
    if not isinstance(raw, dict):
        raise CandidatePromotionError("CANDIDATE_PARAMS_MISSING")
    return params_dict(raw)


def validate_research_candidate(candidate: dict[str, Any], *, candidate_path: Path | None = None) -> dict[str, Any]:
    if not isinstance(candidate, dict):
        raise CandidatePromotionError("CANDIDATE_JSON_INVALID")
    if candidate.get("artifact_type") != STRICT_RESEARCH_CANDIDATE_TYPE:
        raise CandidatePromotionError("CANDIDATE_NOT_STRICT_RESEARCH")
    if str(candidate.get("research_mode", "")) != "FORMAL":
        raise CandidatePromotionError("CANDIDATE_NOT_FORMAL_RESEARCH")
    if not bool(candidate.get("promotion_eligible", False)):
        raise CandidatePromotionError("PROMOTION_NOT_ELIGIBLE")
    semantic_error = _research_semantic_error(candidate)
    if semantic_error is not None:
        raise CandidatePromotionError(f"CANDIDATE_{semantic_error.upper()}")
    blind_status, blind_passed = _candidate_blind_evidence(candidate)
    if blind_status != BLIND_SEALED_PASS or not blind_passed:
        raise CandidatePromotionError("CANDIDATE_BLIND_NOT_SEALED_PASS")
    if not str(candidate.get("research_run_id", "")).strip():
        raise CandidatePromotionError("CANDIDATE_RUN_ID_MISSING")
    if _parse_time(candidate.get("generated_at")) is None:
        raise CandidatePromotionError("CANDIDATE_GENERATED_AT_INVALID")
    params = _candidate_params(candidate)
    expected_params_hash = str(candidate.get("candidate_params_sha256", ""))
    actual_params_hash = canonical_sha256(params)
    if not expected_params_hash:
        raise CandidatePromotionError("CANDIDATE_PARAM_HASH_MISSING")
    if actual_params_hash != expected_params_hash:
        raise CandidatePromotionError("CANDIDATE_PARAM_HASH_MISMATCH")
    if candidate_path is not None:
        _verify_candidate_artifact_hashes(candidate, candidate_path=candidate_path)
    return params


def _verify_candidate_artifact_hashes(candidate: dict[str, Any], *, candidate_path: Path) -> None:
    artifact_hashes = candidate.get("artifact_hashes", {})
    if not isinstance(artifact_hashes, dict):
        raise CandidatePromotionError("CANDIDATE_ARTIFACT_HASHES_INVALID")
    base = candidate_path.parent
    for relative, expected in artifact_hashes.items():
        artifact_path = base / str(relative)
        if not artifact_path.exists():
            raise CandidatePromotionError(f"CANDIDATE_ARTIFACT_MISSING:{relative}")
        actual = file_sha256(artifact_path)
        if actual != str(expected):
            raise CandidatePromotionError(f"CANDIDATE_ARTIFACT_HASH_MISMATCH:{relative}")


def build_approved_manifest(
    candidate: dict[str, Any],
    *,
    source_candidate_path: str | Path | None = None,
    operator: str | None = None,
    approved_at: str | None = None,
) -> dict[str, Any]:
    params = validate_research_candidate(
        candidate,
        candidate_path=Path(source_candidate_path) if source_candidate_path is not None else None,
    )
    source_path = Path(source_candidate_path) if source_candidate_path is not None else None
    source_hash = file_sha256(source_path) if source_path is not None and source_path.exists() else canonical_sha256(candidate)
    candidate_generated_at = str(candidate.get("generated_at") or "")
    approved_time = approved_at or _utc_now_text()
    research_metadata = candidate.get("research_metadata", {}) if isinstance(candidate.get("research_metadata", {}), dict) else {}
    blind_status, _blind_passed = _candidate_blind_evidence(candidate)
    source_parent = source_path.parent.name if source_path is not None else ""
    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "manifest_type": MANIFEST_TYPE,
        "strategy_version": APPROVED_STRATEGY_VERSION,
        "approved_at": approved_time,
        "promotion_approved_at": approved_time,
        "operator": (operator or getpass.getuser()).strip(),
        "source_candidate_path": str(source_path) if source_path is not None else "",
        "source_candidate_sha256": source_hash,
        "candidate_generated_at": candidate_generated_at,
        "research_run_id": str(candidate.get("research_run_id") or source_parent),
        "dataset_identity_hash": str(candidate.get("dataset_identity_hash") or candidate.get("dataset_hash") or research_metadata.get("dataset_identity_hash", "")),
        "config_hash": str(candidate.get("config_hash") or research_metadata.get("config_hash", "")),
        "source_hash": str(candidate.get("source_hash") or research_metadata.get("source_hash") or source_hash),
        "blind_status": blind_status,
        "dataset": str(candidate.get("dataset", "")),
        "signal_timeframe": str(candidate.get("signal_timeframe", "")),
        "trend_timeframe": str(candidate.get("trend_timeframe", "")),
        "research_version": str(candidate.get("research_version", "")),
        "research_mode": str(candidate.get("research_mode", "")),
        "promotion_eligible": bool(candidate.get("promotion_eligible", False)),
        "selected_params": params,
        "selected_params_sha256": canonical_sha256(params),
        "research_metadata": research_metadata,
        "artifact_hashes": candidate.get("artifact_hashes", {}) if isinstance(candidate.get("artifact_hashes", {}), dict) else {},
    }
    manifest["manifest_hash"] = canonical_sha256(_manifest_hash_payload(manifest))
    manifest["manifest_sha256"] = manifest["manifest_hash"]
    return manifest


def validate_approved_manifest(manifest: dict[str, Any]) -> StrategyParams:
    if not isinstance(manifest, dict):
        raise ManifestValidationError("runtime_manifest_invalid_json")
    if manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise ManifestValidationError("runtime_manifest_schema_unsupported")
    if manifest.get("manifest_type") != MANIFEST_TYPE:
        raise ManifestValidationError("runtime_manifest_type_invalid")
    if str(manifest.get("strategy_version", "")) != APPROVED_STRATEGY_VERSION:
        raise ManifestValidationError("runtime_manifest_strategy_version_mismatch")
    if str(manifest.get("research_mode", "")) != "FORMAL":
        raise ManifestValidationError("runtime_manifest_research_mode_invalid")
    if manifest.get("promotion_eligible") is not True:
        raise ManifestValidationError("runtime_manifest_promotion_not_eligible")
    if not str(manifest.get("operator", "")).strip():
        raise ManifestValidationError("runtime_manifest_operator_missing")
    if not str(manifest.get("source_candidate_sha256", "")).strip():
        raise ManifestValidationError("runtime_manifest_source_hash_missing")
    semantic_error = _research_semantic_error(manifest)
    if semantic_error is not None:
        raise ManifestValidationError(f"runtime_manifest_{semantic_error}")
    blind_status, blind_passed = _candidate_blind_evidence(manifest)
    if blind_status != BLIND_SEALED_PASS or not blind_passed:
        raise ManifestValidationError("runtime_manifest_blind_not_sealed_pass")
    if not str(manifest.get("research_run_id", "")).strip():
        raise ManifestValidationError("runtime_manifest_run_id_missing")
    candidate_time = _parse_time(manifest.get("candidate_generated_at"))
    approved_time = _parse_time(manifest.get("approved_at"))
    promotion_time = _parse_time(manifest.get("promotion_approved_at"))
    if candidate_time is None:
        raise ManifestValidationError("runtime_manifest_candidate_time_invalid")
    if approved_time is None or promotion_time is None:
        raise ManifestValidationError("runtime_manifest_approval_time_invalid")
    if approved_time < candidate_time or promotion_time < candidate_time:
        raise ManifestValidationError("runtime_manifest_approval_precedes_candidate")
    expected_manifest_hash = str(manifest.get("manifest_sha256") or manifest.get("manifest_hash", ""))
    actual_manifest_hash = canonical_sha256(_manifest_hash_payload(manifest))
    if not expected_manifest_hash or actual_manifest_hash != expected_manifest_hash:
        raise ManifestValidationError("runtime_manifest_hash_mismatch")
    raw_params = manifest.get("selected_params")
    if not isinstance(raw_params, dict):
        raise ManifestValidationError("runtime_manifest_params_missing")
    expected_params_hash = str(manifest.get("selected_params_sha256", ""))
    actual_params_hash = canonical_sha256(params_dict(raw_params))
    if not expected_params_hash or actual_params_hash != expected_params_hash:
        raise ManifestValidationError("runtime_manifest_param_hash_mismatch")
    return strategy_params_from_dict(raw_params)


def read_approved_manifest(path: str | Path) -> dict[str, Any]:
    target = Path(path)
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ManifestValidationError("runtime_manifest_missing") from exc
    except json.JSONDecodeError as exc:
        raise ManifestValidationError("runtime_manifest_json_invalid") from exc
    if not isinstance(payload, dict):
        raise ManifestValidationError("runtime_manifest_invalid_json")
    return payload


def load_approved_manifest_status(output_dir: str | Path | None = None, *, path: str | Path | None = None) -> ApprovedManifestStatus:
    from okx_signal_system.config import project_paths

    out = Path(output_dir or project_paths().output_dir)
    manifest_path = Path(path) if path is not None else approved_manifest_path(out)
    try:
        manifest = read_approved_manifest(manifest_path)
        params = validate_approved_manifest(manifest)
    except ManifestValidationError as exc:
        return ApprovedManifestStatus(
            path=manifest_path,
            ok=False,
            reason=str(exc),
            params=StrategyParams(),
            manifest={},
        )
    return ApprovedManifestStatus(
        path=manifest_path,
        ok=True,
        reason="approved_manifest_valid",
        params=params,
        manifest=manifest,
    )


def write_approved_manifest_atomic(manifest: dict[str, Any], path: str | Path) -> Path:
    target = Path(path)
    validate_approved_manifest(manifest)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = target.with_name(
        f"{target.stem}.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex}.tmp{target.suffix}"
    )
    try:
        tmp_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        replace_with_retry(tmp_path, target)
    except Exception:
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass
        raise
    return target


def _existing_candidate_time(manifest_path: Path) -> datetime | None:
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    return _parse_time(payload.get("candidate_generated_at"))


def promote_candidate_manifest(
    *,
    output_dir: str | Path,
    run_id: str | None = None,
    candidate_path: str | Path | None = None,
    manifest_path: str | Path | None = None,
    operator: str | None = None,
) -> Path:
    out = Path(output_dir)
    if candidate_path is not None:
        candidate_file = Path(candidate_path)
    elif run_id is not None:
        candidate_file = candidate_params_path(out, run_id)
    else:
        raise CandidatePromotionError("RUN_ID_REQUIRED")
    target_manifest = Path(manifest_path) if manifest_path is not None else approved_manifest_path(out)
    try:
        candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CandidatePromotionError("CANDIDATE_FILE_MISSING") from exc
    except json.JSONDecodeError as exc:
        raise CandidatePromotionError("CANDIDATE_JSON_INVALID") from exc
    if not isinstance(candidate, dict):
        raise CandidatePromotionError("CANDIDATE_JSON_INVALID")

    candidate_time = _parse_time(candidate.get("generated_at"))
    existing_time = _existing_candidate_time(target_manifest)
    if candidate_time is not None and existing_time is not None and candidate_time <= existing_time:
        raise CandidatePromotionError("STALE_CANDIDATE_ARTIFACT")

    manifest = build_approved_manifest(candidate, source_candidate_path=candidate_file, operator=operator)
    return write_approved_manifest_atomic(manifest, target_manifest)
