from __future__ import annotations

"""Parallel forward-acceptance governance for research-only candidates.

The module is deliberately isolated from the formal signal runtime. It can:

* read already-produced prospective evidence for multiple independent tracks;
* require a passed frozen research-gate report before a new track is admitted;
* apply frozen frequency-aware checkpoint and sample-profile rules;
* send explicitly labelled research-only Feishu summaries;
* permanently archive tracks that hit a frozen failure rule.

It never promotes a strategy, changes parameters, places orders, or changes the
formal A-tier signal list. Formal promotion remains a separate manual action.
"""

import hashlib
import json
import os
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

from okx_signal_system.config import project_paths
from okx_signal_system.io_atomic import write_text_atomic
from okx_signal_system.paths import workspace_root

VARIANTS = ("original", "hysteresis_4_in_6_out")
DEFAULT_VARIANT_LABELS = {
    "original": "原始14日动量",
    "hysteresis_4_in_6_out": "固定4入6出",
}
INTEGRITY_FIELDS = (
    "protocol_integrity",
    "ledger_integrity",
    "data_quality_integrity",
    "daily_snapshot_chain_integrity",
)
FROZEN_EARLY_STOP_PROTOCOL_SHA256 = "0aaba2e9c90037e836eb40505c66bd9affa7c954bdaca504794925c43e64f5e5"


@dataclass(frozen=True)
class ForwardSampleProfile:
    """Frequency-aware forward-sample requirements.

    The default profile exactly preserves the original daily-track rules. Slow
    tracks may stretch calendar checkpoints to the same number of scheduled
    observation opportunities and may use a non-terminal minimum review before
    the unchanged final manual-review sample is reached.
    """

    cadence_days: int = 1
    minimum_calendar_days: int = 60
    minimum_closed_observations: int = 50
    preferred_calendar_days: int = 90
    preferred_closed_observations: int = 50
    minimum_failure_terminal: bool = True

    def checkpoint_calendar_day(self, base_day: int) -> int:
        return int(base_day) * int(self.cadence_days)


DEFAULT_FORWARD_SAMPLE_PROFILE = ForwardSampleProfile()


@dataclass(frozen=True)
class AcceptanceTrackConfig:
    track_id: str
    label: str
    source_status: Path
    source_ledger: Path
    variants: tuple[str, ...]
    variant_labels: tuple[tuple[str, str], ...]
    updater_script: Path | None = None
    admission_report: Path | None = None
    admission_exempt_frozen_reference: bool = False
    sample_profile: ForwardSampleProfile = DEFAULT_FORWARD_SAMPLE_PROFILE

    def label_for(self, variant: str) -> str:
        labels = dict(self.variant_labels)
        return labels.get(variant, variant)


@dataclass(frozen=True)
class ParallelAcceptanceConfig:
    enabled: bool
    source_status: Path
    source_ledger: Path
    output_status: Path
    notification_state: Path
    notification_enabled: bool
    day14_min_rebalances: int
    day30_min_rebalances: int
    day30_min_closed_per_variant: int
    day30_base_pf_catastrophic_below: float
    day30_stress_pf_catastrophic_below: float
    day30_base_return_catastrophic_below: float
    day30_stress_return_catastrophic_below: float
    day45_min_rebalances: int
    day45_min_closed_per_variant: int
    day45_symbol_concentration_catastrophic_above: float
    day45_top5_concentration_catastrophic_above: float
    archive_root: Path | None = None
    tracks: tuple[AcceptanceTrackConfig, ...] = ()
    early_stop_protocol_path: Path | None = None
    early_stop_protocol_sha256: str | None = None
    early_stop_protocol: dict[str, Any] | None = None


def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    if value is None:
        return default
    return bool(value)


def _resolve_path(value: Any, *, base: Path) -> Path:
    path = Path(str(value)).expanduser()
    return path if path.is_absolute() else base / path


def _default_failure_archive_root() -> Path:
    configured = os.environ.get("FAILED_RESEARCH_ARCHIVE_DIR", "").strip()
    if configured:
        return Path(configured).expanduser()
    for name in ("失败策略", "失败策略文件夹", "Failed Strategies"):
        desktop = Path.home() / "Desktop" / name
        if desktop.is_dir():
            return desktop
    return project_paths().root / "outputs" / "failed_research"


def _parse_variants(raw: Any) -> tuple[tuple[str, ...], tuple[tuple[str, str], ...]]:
    if isinstance(raw, Mapping):
        variants = tuple(str(key).strip() for key in raw if str(key).strip())
        labels = tuple((str(key), str(value)) for key, value in raw.items() if str(key).strip())
        return variants, labels
    if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
        variants = tuple(str(item).strip() for item in raw if str(item).strip())
        labels = tuple((item, DEFAULT_VARIANT_LABELS.get(item, item)) for item in variants)
        return variants, labels
    return VARIANTS, tuple(DEFAULT_VARIANT_LABELS.items())


def _sample_profile_from_mapping(raw: Any) -> ForwardSampleProfile:
    if raw is None:
        return DEFAULT_FORWARD_SAMPLE_PROFILE
    if not isinstance(raw, Mapping):
        raise ValueError("parallel acceptance sample_profile must be a mapping")
    profile = ForwardSampleProfile(
        cadence_days=int(raw.get("cadence_days", 1)),
        minimum_calendar_days=int(raw.get("minimum_calendar_days", 60)),
        minimum_closed_observations=int(raw.get("minimum_closed_observations", 50)),
        preferred_calendar_days=int(raw.get("preferred_calendar_days", 90)),
        preferred_closed_observations=int(raw.get("preferred_closed_observations", 50)),
        minimum_failure_terminal=_as_bool(raw.get("minimum_failure_terminal"), True),
    )
    if profile.cadence_days < 1:
        raise ValueError("sample_profile.cadence_days must be at least 1")
    if profile.minimum_calendar_days < 1 or profile.minimum_closed_observations < 1:
        raise ValueError("sample_profile minimum sample requirements must be positive")
    if profile.preferred_calendar_days < profile.minimum_calendar_days:
        raise ValueError("sample_profile preferred_calendar_days cannot be below minimum_calendar_days")
    if profile.preferred_closed_observations < profile.minimum_closed_observations:
        raise ValueError(
            "sample_profile preferred_closed_observations cannot be below minimum_closed_observations"
        )
    return profile


def _track_from_mapping(raw: Mapping[str, Any], *, ws_root: Path, index: int) -> AcceptanceTrackConfig:
    variants, labels = _parse_variants(raw.get("variants"))
    if not variants:
        raise ValueError(f"parallel acceptance track {index} has no variants")
    track_id = str(raw.get("track_id") or f"track_{index}").strip()
    if not track_id:
        raise ValueError(f"parallel acceptance track {index} has an empty track_id")
    source_status = raw.get("source_status")
    source_ledger = raw.get("source_ledger")
    if not source_status or not source_ledger:
        raise ValueError(f"parallel acceptance track {track_id} requires source_status and source_ledger")
    updater_value = raw.get("updater_script")
    admission_value = raw.get("admission_report")
    return AcceptanceTrackConfig(
        track_id=track_id,
        label=str(raw.get("label") or track_id),
        source_status=_resolve_path(source_status, base=ws_root),
        source_ledger=_resolve_path(source_ledger, base=ws_root),
        variants=variants,
        variant_labels=labels,
        updater_script=_resolve_path(updater_value, base=ws_root) if updater_value else None,
        admission_report=_resolve_path(admission_value, base=ws_root) if admission_value else None,
        admission_exempt_frozen_reference=_as_bool(raw.get("admission_exempt_frozen_reference"), False),
        sample_profile=_sample_profile_from_mapping(raw.get("sample_profile")),
    )


def load_parallel_acceptance_config(path: str | Path | None = None) -> ParallelAcceptanceConfig:
    paths = project_paths()
    config_path = Path(path) if path else paths.config_dir / "parallel_acceptance.yaml"
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    values = raw.get("parallel_acceptance", raw)
    if not isinstance(values, Mapping):
        raise ValueError("parallel_acceptance config must be a mapping")

    ws_root = workspace_root()
    protocol_path = _resolve_path(
        values.get(
            "early_stop_protocol",
            "config/parallel_acceptance_early_stop_protocol.json",
        ),
        base=paths.root,
    )
    early_stop_protocol, early_stop_protocol_sha256 = _load_frozen_early_stop_protocol(protocol_path)
    checkpoints = early_stop_protocol.get("checkpoints", {})
    checkpoints = checkpoints if isinstance(checkpoints, Mapping) else {}
    day14 = checkpoints.get("day14", {}) if isinstance(checkpoints.get("day14"), Mapping) else {}
    day30 = checkpoints.get("day30", {}) if isinstance(checkpoints.get("day30"), Mapping) else {}
    day45 = checkpoints.get("day45", {}) if isinstance(checkpoints.get("day45"), Mapping) else {}

    tracks_raw = values.get("tracks")
    tracks: tuple[AcceptanceTrackConfig, ...]
    if isinstance(tracks_raw, Sequence) and not isinstance(tracks_raw, (str, bytes)):
        parsed = [
            _track_from_mapping(item, ws_root=ws_root, index=index)
            for index, item in enumerate(tracks_raw, start=1)
            if isinstance(item, Mapping)
        ]
        if not parsed:
            raise ValueError("parallel_acceptance.tracks must contain at least one valid mapping")
        ids = [item.track_id for item in parsed]
        if len(ids) != len(set(ids)):
            raise ValueError("parallel_acceptance track_id values must be unique")
        tracks = tuple(parsed)
        legacy_status = tracks[0].source_status
        legacy_ledger = tracks[0].source_ledger
    else:
        legacy_status = _resolve_path(values["source_status"], base=ws_root)
        legacy_ledger = _resolve_path(values["source_ledger"], base=ws_root)
        tracks = ()

    archive_value = values.get("archive_root")
    archive_root = _resolve_path(archive_value, base=paths.root) if archive_value else _default_failure_archive_root()

    return ParallelAcceptanceConfig(
        enabled=_as_bool(values.get("enabled"), True),
        source_status=legacy_status,
        source_ledger=legacy_ledger,
        output_status=_resolve_path(values.get("output_status", "outputs/parallel_acceptance_status.json"), base=paths.root),
        notification_state=_resolve_path(
            values.get("notification_state", "outputs/parallel_acceptance_notification_state.json"),
            base=paths.root,
        ),
        notification_enabled=_as_bool(values.get("notification_enabled"), True),
        day14_min_rebalances=int(day14.get("minimum_rebalances", 10)),
        day30_min_rebalances=int(day30.get("minimum_rebalances", 20)),
        day30_min_closed_per_variant=int(day30.get("minimum_closed_per_variant", 20)),
        day30_base_pf_catastrophic_below=float(day30.get("base_pf_catastrophic_below", 0.70)),
        day30_stress_pf_catastrophic_below=float(day30.get("stress_pf_catastrophic_below", 0.60)),
        day30_base_return_catastrophic_below=float(day30.get("base_return_catastrophic_below", -0.08)),
        day30_stress_return_catastrophic_below=float(day30.get("stress_return_catastrophic_below", -0.10)),
        day45_min_rebalances=int(day45.get("minimum_rebalances", 35)),
        day45_min_closed_per_variant=int(day45.get("minimum_closed_per_variant", 35)),
        day45_symbol_concentration_catastrophic_above=float(
            day45.get("single_symbol_positive_share_catastrophic_above", 0.60)
        ),
        day45_top5_concentration_catastrophic_above=float(
            day45.get("top5_positive_share_catastrophic_above", 0.85)
        ),
        archive_root=archive_root,
        tracks=tracks,
        early_stop_protocol_path=protocol_path,
        early_stop_protocol_sha256=early_stop_protocol_sha256,
        early_stop_protocol=early_stop_protocol,
    )


def configured_tracks(config: ParallelAcceptanceConfig) -> tuple[AcceptanceTrackConfig, ...]:
    if config.tracks:
        return config.tracks
    return (
        AcceptanceTrackConfig(
            track_id="momentum_14d_and_4in6out",
            label="14日动量与固定4入6出",
            source_status=config.source_status,
            source_ledger=config.source_ledger,
            variants=VARIANTS,
            variant_labels=tuple(DEFAULT_VARIANT_LABELS.items()),
            admission_exempt_frozen_reference=True,
        ),
    )


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON object required: {path}")
    return payload


def _canonical_sha256(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _load_frozen_early_stop_protocol(path: Path) -> tuple[dict[str, Any], str]:
    protocol = _read_json(path)
    digest = _canonical_sha256(protocol)
    if digest != FROZEN_EARLY_STOP_PROTOCOL_SHA256:
        raise ValueError(
            f"parallel early-stop protocol checksum invalid: {digest}"
        )
    if protocol.get("schema") != "okx_parallel_early_stop_protocol_v1":
        raise ValueError("parallel early-stop protocol schema invalid")
    return protocol, digest


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def _pf(summary: Mapping[str, Any], key: str) -> float | None:
    block = summary.get(key)
    if not isinstance(block, Mapping):
        return None
    if block.get("profit_factor_infinite") is True:
        return float("inf")
    return _finite(block.get("profit_factor"))


def _total_return(summary: Mapping[str, Any], key: str) -> float | None:
    block = summary.get(key)
    if not isinstance(block, Mapping):
        return None
    return _finite(block.get("total_return"))


def _checkpoint_due(days: int, threshold: int) -> bool:
    return int(days) >= int(threshold)


def _integrity_failures(status: Mapping[str, Any]) -> list[str]:
    return [field for field in INTEGRITY_FIELDS if str(status.get(field, "")).upper() != "PASS"]


def _final_gate_state(status: Mapping[str, Any], variant: str) -> tuple[bool | None, str]:
    results = status.get("variant_fixed_gate_results")
    if not isinstance(results, Mapping):
        return None, "fixed_gate_result_missing"
    result = results.get(variant)
    if not isinstance(result, Mapping):
        return None, "fixed_gate_result_missing"
    all_pass = result.get("all_pass")
    if all_pass is True:
        return True, str(result.get("status") or "PASS")
    if all_pass is False:
        return False, str(result.get("status") or "FAIL")
    return None, str(result.get("status") or "NOT_EVALUATED_SAMPLE_INCOMPLETE")


def evaluate_variant(
    status: Mapping[str, Any],
    variant: str,
    config: ParallelAcceptanceConfig,
    sample_profile: ForwardSampleProfile = DEFAULT_FORWARD_SAMPLE_PROFILE,
) -> dict[str, Any]:
    days = int(status.get("elapsed_closed_data_days") or 0)
    rebalances = int(status.get("fully_prospective_rebalance_count") or 0)
    day14_due_at = sample_profile.checkpoint_calendar_day(14)
    day30_due_at = sample_profile.checkpoint_calendar_day(30)
    day45_due_at = sample_profile.checkpoint_calendar_day(45)
    variants = status.get("variants")
    summary = variants.get(variant, {}) if isinstance(variants, Mapping) else {}
    summary = summary if isinstance(summary, Mapping) else {}
    closed = int(summary.get("closed_count") or 0)
    integrity_failures = _integrity_failures(status)

    checks: dict[str, Any] = {
        "integrity": not integrity_failures,
        "day14_health": None,
        "day30_activity": None,
        "day30_catastrophic_economics": None,
        "day45_activity": None,
        "day45_catastrophic_concentration": None,
        "minimum_fixed_gate": None,
        "preferred_fixed_gate": None,
    }
    reasons: list[str] = []
    stage = "RESEARCH_SHADOW"
    decision = "RECORD_ONLY"

    if integrity_failures:
        stage = "EVIDENCE_BLOCKED"
        decision = "FAIL_CLOSED_EVIDENCE_CHAIN"
        reasons.extend(f"integrity_failed:{field}" for field in integrity_failures)
    else:
        if _checkpoint_due(days, day14_due_at):
            checks["day14_health"] = rebalances >= config.day14_min_rebalances
            if not checks["day14_health"]:
                stage = "EVIDENCE_BLOCKED"
                decision = "PAUSE_AND_REPAIR_EVIDENCE_CHAIN"
                reasons.append("day14_rebalance_capture_severely_incomplete")

        if stage != "EVIDENCE_BLOCKED" and _checkpoint_due(days, day30_due_at):
            checks["day30_activity"] = (
                rebalances >= config.day30_min_rebalances
                and closed >= config.day30_min_closed_per_variant
            )
            if not checks["day30_activity"]:
                stage = "EVIDENCE_BLOCKED"
                decision = "PAUSE_AND_REPAIR_EVIDENCE_CHAIN"
                reasons.append("day30_forward_sample_capture_severely_incomplete")
            else:
                base_pf = _pf(summary, "base")
                stress_pf = _pf(summary, "stress")
                base_return = _total_return(summary, "base")
                stress_return = _total_return(summary, "stress")
                catastrophic = (
                    base_pf is not None
                    and stress_pf is not None
                    and base_return is not None
                    and stress_return is not None
                    and base_pf < config.day30_base_pf_catastrophic_below
                    and stress_pf < config.day30_stress_pf_catastrophic_below
                    and base_return < config.day30_base_return_catastrophic_below
                    and stress_return < config.day30_stress_return_catastrophic_below
                )
                checks["day30_catastrophic_economics"] = not catastrophic
                if catastrophic:
                    stage = "FAILED_ARCHIVE"
                    decision = "EARLY_TERMINATE_FROZEN_DAY30_RULE"
                    reasons.append("day30_catastrophic_cost_adjusted_failure")

        if stage not in {"EVIDENCE_BLOCKED", "FAILED_ARCHIVE"} and _checkpoint_due(days, day45_due_at):
            checks["day45_activity"] = (
                rebalances >= config.day45_min_rebalances
                and closed >= config.day45_min_closed_per_variant
            )
            if not checks["day45_activity"]:
                stage = "EVIDENCE_BLOCKED"
                decision = "PAUSE_AND_REPAIR_EVIDENCE_CHAIN"
                reasons.append("day45_forward_sample_capture_severely_incomplete")
            else:
                symbol_share = _finite(summary.get("single_symbol_positive_contribution_share"))
                top5_share = _finite(summary.get("top_5_profitable_rebalances_positive_contribution_share"))
                catastrophic = (
                    symbol_share is not None
                    and top5_share is not None
                    and symbol_share > config.day45_symbol_concentration_catastrophic_above
                    and top5_share > config.day45_top5_concentration_catastrophic_above
                )
                checks["day45_catastrophic_concentration"] = not catastrophic
                if catastrophic:
                    stage = "FAILED_ARCHIVE"
                    decision = "EARLY_TERMINATE_FROZEN_DAY45_RULE"
                    reasons.append("day45_extreme_symbol_and_few_trade_concentration")

        minimum_due = (
            days >= sample_profile.minimum_calendar_days
            and closed >= sample_profile.minimum_closed_observations
        )
        preferred_due = (
            days >= sample_profile.preferred_calendar_days
            and closed >= sample_profile.preferred_closed_observations
        )
        fixed_pass, fixed_status = _final_gate_state(status, variant)
        if stage not in {"EVIDENCE_BLOCKED", "FAILED_ARCHIVE"} and minimum_due:
            checks["minimum_fixed_gate"] = fixed_pass
            if fixed_pass is False and sample_profile.minimum_failure_terminal:
                stage = "FAILED_ARCHIVE"
                decision = "ARCHIVE_FIXED_GATE_FAILURE"
                reasons.append(f"minimum_fixed_gate_failed:{fixed_status}")
            elif fixed_pass is False:
                stage = "RESEARCH_SHADOW"
                decision = "CONTINUE_TO_PREFERRED_CONFIRMATION_MINIMUM_GATE_NOT_YET_PASSED"
                reasons.append(f"minimum_fixed_gate_not_yet_passed:{fixed_status}")
            elif fixed_pass is True:
                stage = "FORWARD_SURVIVOR"
                decision = "CONTINUE_TO_PREFERRED_CONFIRMATION"
            else:
                stage = "EVIDENCE_BLOCKED"
                decision = "FIXED_GATE_RESULT_MISSING_OR_INCOMPLETE"
                reasons.append(f"minimum_fixed_gate_unavailable:{fixed_status}")

        if stage not in {"EVIDENCE_BLOCKED", "FAILED_ARCHIVE"} and preferred_due:
            checks["preferred_fixed_gate"] = fixed_pass
            if fixed_pass is True:
                stage = "FORMAL_A_REVIEW_READY"
                decision = "MANUAL_REVIEW_REQUIRED_NO_AUTO_PROMOTION"
            elif fixed_pass is False:
                stage = "FAILED_ARCHIVE"
                decision = "ARCHIVE_PREFERRED_FIXED_GATE_FAILURE"
                reasons.append(f"preferred_fixed_gate_failed:{fixed_status}")
            else:
                stage = "EVIDENCE_BLOCKED"
                decision = "PREFERRED_FIXED_GATE_RESULT_MISSING_OR_INCOMPLETE"
                reasons.append(f"preferred_fixed_gate_unavailable:{fixed_status}")

    return {
        "variant": variant,
        "signal_level": "研究级/影子信号",
        "stage": stage,
        "decision": decision,
        "formal_a": False,
        "automatic_promotion": False,
        "automatic_ordering": False,
        "parameter_mutation_allowed": False,
        "closed_observations": closed,
        "sample_requirements": {
            "cadence_days": sample_profile.cadence_days,
            "day14_equivalent_calendar_day": day14_due_at,
            "day30_equivalent_calendar_day": day30_due_at,
            "day45_equivalent_calendar_day": day45_due_at,
            "minimum_calendar_days": sample_profile.minimum_calendar_days,
            "minimum_closed_observations": sample_profile.minimum_closed_observations,
            "preferred_calendar_days": sample_profile.preferred_calendar_days,
            "preferred_closed_observations": sample_profile.preferred_closed_observations,
            "minimum_failure_terminal": sample_profile.minimum_failure_terminal,
        },
        "checks": checks,
        "reasons": reasons,
    }


def build_parallel_acceptance_status(
    source_status: Mapping[str, Any],
    *,
    source_status_sha256: str,
    source_ledger_sha256: str,
    config: ParallelAcceptanceConfig,
    variants: Sequence[str] = VARIANTS,
    sample_profile: ForwardSampleProfile = DEFAULT_FORWARD_SAMPLE_PROFILE,
) -> dict[str, Any]:
    variant_results = {
        variant: evaluate_variant(source_status, variant, config, sample_profile)
        for variant in variants
    }
    stages = {item["stage"] for item in variant_results.values()}
    if "FAILED_ARCHIVE" in stages:
        overall = "PARTIAL_OR_FULL_FAILURE"
    elif "EVIDENCE_BLOCKED" in stages:
        overall = "EVIDENCE_BLOCKED"
    elif stages == {"FORMAL_A_REVIEW_READY"}:
        overall = "FORMAL_A_REVIEW_READY"
    elif "FORWARD_SURVIVOR" in stages:
        overall = "FORWARD_SURVIVOR"
    else:
        overall = "RESEARCH_SHADOW_RUNNING"

    return {
        "schema": "okx_parallel_forward_acceptance_track_v2",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "overall_stage": overall,
        "signal_policy": {
            "label": "研究级/影子信号",
            "formal_a_allowed": False,
            "automatic_ordering": False,
            "automatic_parameter_changes": False,
            "manual_review_required_for_promotion": True,
        },
        "source": {
            "acceptance_protocol_id": source_status.get("acceptance_protocol_id"),
            "candidate_protocol_id": source_status.get("candidate_protocol_id"),
            "source_status_sha256": source_status_sha256,
            "source_ledger_sha256": source_ledger_sha256,
            "elapsed_closed_data_days": int(source_status.get("elapsed_closed_data_days") or 0),
            "fully_prospective_rebalance_count": int(
                source_status.get("fully_prospective_rebalance_count") or 0
            ),
            "closed_data_through_utc": source_status.get("closed_data_through_utc"),
        },
        "early_stop_protocol": {
            "path": str(config.early_stop_protocol_path) if config.early_stop_protocol_path else None,
            "sha256": config.early_stop_protocol_sha256,
            "schema": (config.early_stop_protocol or {}).get("schema"),
            "frozen_at_utc": (config.early_stop_protocol or {}).get("frozen_at_utc"),
            "known_sample_at_freeze": (config.early_stop_protocol or {}).get("known_sample_at_freeze"),
            "calibration_prohibition": (config.early_stop_protocol or {}).get("calibration_prohibition"),
            "retroactivity": (config.early_stop_protocol or {}).get("retroactivity"),
        },
        "sample_profile": {
            "cadence_days": sample_profile.cadence_days,
            "minimum_calendar_days": sample_profile.minimum_calendar_days,
            "minimum_closed_observations": sample_profile.minimum_closed_observations,
            "preferred_calendar_days": sample_profile.preferred_calendar_days,
            "preferred_closed_observations": sample_profile.preferred_closed_observations,
            "minimum_failure_terminal": sample_profile.minimum_failure_terminal,
        },
        "frozen_early_checkpoints": {
            "health_calendar_day": sample_profile.checkpoint_calendar_day(14),
            "health_rule": "health_and_evidence_chain_only",
            "catastrophic_economics_calendar_day": sample_profile.checkpoint_calendar_day(30),
            "catastrophic_economics_rule": "severe_sample_capture_or_catastrophic_cost_adjusted_failure_only",
            "catastrophic_concentration_calendar_day": sample_profile.checkpoint_calendar_day(45),
            "catastrophic_concentration_rule": "severe_sample_capture_or_joint_extreme_concentration_only",
            "minimum_review": (
                f"day{sample_profile.minimum_calendar_days}_and_"
                f"{sample_profile.minimum_closed_observations}_closed_observations"
            ),
            "preferred_review": (
                f"day{sample_profile.preferred_calendar_days}_and_"
                f"{sample_profile.preferred_closed_observations}_closed_observations"
            ),
        },
        "variants": variant_results,
    }


def evaluate_track_admission(track: AcceptanceTrackConfig) -> dict[str, Any]:
    if track.admission_exempt_frozen_reference:
        return {
            "status": "PASS",
            "reason": "pre_existing_frozen_reference",
            "report": None,
            "report_sha256": None,
        }
    if track.admission_report is None:
        return {
            "status": "BLOCKED",
            "reason": "admission_report_required_for_new_candidate",
            "report": None,
            "report_sha256": None,
        }
    if not track.admission_report.is_file():
        return {
            "status": "BLOCKED",
            "reason": "admission_report_missing",
            "report": str(track.admission_report),
            "report_sha256": None,
        }
    try:
        report = _read_json(track.admission_report)
    except Exception as exc:
        return {
            "status": "BLOCKED",
            "reason": f"admission_report_invalid:{exc}",
            "report": str(track.admission_report),
            "report_sha256": _sha256_file(track.admission_report),
        }
    if report.get("schema") != "okx_research_gate_report_v2":
        return {
            "status": "BLOCKED",
            "reason": "admission_report_schema_mismatch",
            "report": str(track.admission_report),
            "report_sha256": _sha256_file(track.admission_report),
        }
    if report.get("ok") is not True:
        return {
            "status": "BLOCKED",
            "reason": "research_gate_not_passed",
            "report": str(track.admission_report),
            "report_sha256": _sha256_file(track.admission_report),
        }
    return {
        "status": "PASS",
        "reason": "frozen_research_gate_passed",
        "report": str(track.admission_report),
        "report_sha256": _sha256_file(track.admission_report),
    }


def latest_research_signal_key(ledger: Mapping[str, Any], variants: Sequence[str] = VARIANTS) -> str | None:
    timestamps: list[str] = []
    for variant in variants:
        block = ledger.get(variant)
        observations = block.get("observations") if isinstance(block, Mapping) else None
        if isinstance(observations, list) and observations:
            value = observations[-1].get("detected_and_entry_utc")
            if value:
                timestamps.append(str(value))
    if not timestamps:
        return None
    return max(timestamps)


def format_research_shadow_message(
    governance: Mapping[str, Any],
    ledger: Mapping[str, Any],
    *,
    track_label: str = "14日动量与固定4入6出",
    variants: Sequence[str] = VARIANTS,
    variant_labels: Mapping[str, str] | None = None,
) -> str | None:
    signal_key = latest_research_signal_key(ledger, variants)
    if signal_key is None:
        return None
    labels = dict(DEFAULT_VARIANT_LABELS)
    if variant_labels:
        labels.update({str(key): str(value) for key, value in variant_labels.items()})
    lines = [
        "OKX 研究级/影子信号（非A级）",
        f"候选轨道: {track_label}",
        "用途: 仅前向观察与证据积累，不代表已证明正期望",
        "执行限制: 不自动下单、不自动持仓、不自动调参、不提供杠杆放大",
        f"理论换仓时间: {signal_key}",
        f"验收阶段: {governance.get('overall_stage', 'UNKNOWN')}",
    ]
    source = governance.get("source")
    if isinstance(source, Mapping):
        lines.append(
            f"前向样本: {int(source.get('elapsed_closed_data_days') or 0)}天 / "
            f"{int(source.get('fully_prospective_rebalance_count') or 0)}次完整换仓"
        )
    for variant in variants:
        block = ledger.get(variant)
        observations = block.get("observations") if isinstance(block, Mapping) else None
        if not isinstance(observations, list) or not observations:
            continue
        latest = observations[-1]
        longs = ", ".join(str(item) for item in latest.get("longs", [])) or "无"
        shorts = ", ".join(str(item) for item in latest.get("shorts", [])) or "无"
        variant_status = governance.get("variants", {}).get(variant, {})
        stage = variant_status.get("stage", "RESEARCH_SHADOW") if isinstance(variant_status, Mapping) else "RESEARCH_SHADOW"
        lines.extend(
            [
                f"[{labels.get(variant, variant)}] 阶段: {stage}",
                f"多头观察: {longs}",
                f"空头观察: {shorts}",
            ]
        )
    lines.append("标记: RESEARCH_ONLY / NOT_A_TIER / SIGNAL_ONLY")
    return "\n".join(lines)


def _safe_id(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in value)


def archive_failed_track(
    track: AcceptanceTrackConfig,
    governance: Mapping[str, Any],
    *,
    archive_root: Path,
) -> Path | None:
    failed = {
        key: value
        for key, value in (governance.get("variants") or {}).items()
        if isinstance(value, Mapping) and value.get("stage") == "FAILED_ARCHIVE"
    }
    if not failed:
        return None
    source = governance.get("source") if isinstance(governance.get("source"), Mapping) else {}
    material = {
        "track_id": track.track_id,
        "status_sha256": source.get("source_status_sha256"),
        "ledger_sha256": source.get("source_ledger_sha256"),
        "failed": failed,
    }
    digest = hashlib.sha256(
        json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    destination = archive_root / f"{_safe_id(track.track_id)}_{digest[:12]}"
    summary_path = destination / "failure_summary.json"
    if summary_path.is_file():
        return destination
    destination.mkdir(parents=True, exist_ok=False)
    shutil.copy2(track.source_status, destination / track.source_status.name)
    shutil.copy2(track.source_ledger, destination / track.source_ledger.name)
    if track.admission_report and track.admission_report.is_file():
        shutil.copy2(track.admission_report, destination / track.admission_report.name)
    write_text_atomic(
        json.dumps(
            {
                "schema": "okx_forward_failure_archive_v1",
                "archived_at_utc": datetime.now(timezone.utc).isoformat(),
                "track_id": track.track_id,
                "track_label": track.label,
                "permanent_rule": "DO_NOT_RETUNE_OR_RENAME_TO_RESCUE",
                "failed_variants": failed,
                "source": source,
            },
            ensure_ascii=False,
            indent=2,
        ),
        summary_path,
    )
    write_text_atomic(
        "该候选已触发冻结失败规则并永久归档。禁止通过改名、事后调参或选择性删样本营救。\n",
        destination / "禁止调参营救.txt",
    )
    return destination


def _blocked_track_status(track: AcceptanceTrackConfig, admission: Mapping[str, Any]) -> dict[str, Any]:
    variants = {
        variant: {
            "variant": variant,
            "signal_level": "未准入",
            "stage": "ADMISSION_BLOCKED",
            "decision": "COMPLETE_RESEARCH_GATES_BEFORE_FORWARD_SHADOW",
            "formal_a": False,
            "automatic_promotion": False,
            "automatic_ordering": False,
            "parameter_mutation_allowed": False,
            "closed_observations": 0,
            "checks": {},
            "reasons": [str(admission.get("reason") or "admission_blocked")],
        }
        for variant in track.variants
    }
    return {
        "schema": "okx_parallel_forward_acceptance_track_v2",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "track_id": track.track_id,
        "track_label": track.label,
        "overall_stage": "ADMISSION_BLOCKED",
        "admission": dict(admission),
        "signal_policy": {
            "label": "未准入",
            "formal_a_allowed": False,
            "automatic_ordering": False,
            "automatic_parameter_changes": False,
            "manual_review_required_for_promotion": True,
        },
        "variants": variants,
    }


def _aggregate_stage(track_results: Mapping[str, Mapping[str, Any]]) -> str:
    stages = {str(item.get("overall_stage") or "UNKNOWN") for item in track_results.values()}
    if "PARTIAL_OR_FULL_FAILURE" in stages:
        return "PARTIAL_OR_FULL_FAILURE"
    if "EVIDENCE_BLOCKED" in stages or "ADMISSION_BLOCKED" in stages:
        return "BLOCKED_TRACKS_PRESENT"
    if stages and stages == {"FORMAL_A_REVIEW_READY"}:
        return "FORMAL_A_REVIEW_READY"
    if "FORMAL_A_REVIEW_READY" in stages or "FORWARD_SURVIVOR" in stages:
        return "FORWARD_SURVIVORS_PRESENT"
    return "RESEARCH_SHADOWS_RUNNING"


def run_parallel_acceptance(
    config: ParallelAcceptanceConfig | None = None,
    *,
    notify: bool = True,
) -> dict[str, Any]:
    config = config or load_parallel_acceptance_config()
    if not config.enabled:
        return {"schema": "okx_parallel_forward_acceptance_v2", "overall_stage": "DISABLED"}

    track_results: dict[str, dict[str, Any]] = {}
    ledgers: dict[str, dict[str, Any]] = {}
    archives: dict[str, str] = {}
    for track in configured_tracks(config):
        admission = evaluate_track_admission(track)
        if admission["status"] != "PASS":
            track_results[track.track_id] = _blocked_track_status(track, admission)
            continue
        if not track.source_status.is_file():
            raise FileNotFoundError(f"forward acceptance status missing for {track.track_id}: {track.source_status}")
        if not track.source_ledger.is_file():
            raise FileNotFoundError(f"forward shadow ledger missing for {track.track_id}: {track.source_ledger}")

        source_status = _read_json(track.source_status)
        ledger = _read_json(track.source_ledger)
        governance = build_parallel_acceptance_status(
            source_status,
            source_status_sha256=_sha256_file(track.source_status),
            source_ledger_sha256=_sha256_file(track.source_ledger),
            config=config,
            variants=track.variants,
            sample_profile=track.sample_profile,
        )
        governance["track_id"] = track.track_id
        governance["track_label"] = track.label
        governance["admission"] = admission
        governance["variant_labels"] = dict(track.variant_labels)
        if config.archive_root is not None:
            archived = archive_failed_track(track, governance, archive_root=config.archive_root)
            if archived is not None:
                archives[track.track_id] = str(archived)
                governance["failure_archive"] = str(archived)
        track_results[track.track_id] = governance
        ledgers[track.track_id] = ledger

    aggregate: dict[str, Any] = {
        "schema": "okx_parallel_forward_acceptance_v2",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "overall_stage": _aggregate_stage(track_results),
        "signal_policy": {
            "research_shadow_push_allowed": True,
            "formal_a_allowed": False,
            "automatic_ordering": False,
            "automatic_parameter_changes": False,
            "manual_review_required_for_promotion": True,
        },
        "tracks": track_results,
        "failure_archives": archives,
    }

    notification: dict[str, Any] = {
        "attempted": 0,
        "sent": 0,
        "failed": 0,
        "skipped": 0,
        "tracks": {},
    }
    if notify and config.notification_enabled:
        state = _read_json(config.notification_state) if config.notification_state.is_file() else {}
        sent_keys = state.get("last_sent_signal_keys")
        sent_keys = dict(sent_keys) if isinstance(sent_keys, Mapping) else {}
        tracks_by_id = {track.track_id: track for track in configured_tracks(config)}
        for track_id, governance in track_results.items():
            track = tracks_by_id[track_id]
            ledger = ledgers.get(track_id)
            if ledger is None or governance.get("overall_stage") == "ADMISSION_BLOCKED":
                notification["skipped"] += 1
                notification["tracks"][track_id] = "admission_blocked_or_no_ledger"
                continue
            key = latest_research_signal_key(ledger, track.variants)
            if not key or sent_keys.get(track_id) == key:
                notification["skipped"] += 1
                notification["tracks"][track_id] = "already_sent_or_no_signal"
                continue
            message = format_research_shadow_message(
                governance,
                ledger,
                track_label=track.label,
                variants=track.variants,
                variant_labels=dict(track.variant_labels),
            )
            if not message:
                notification["skipped"] += 1
                notification["tracks"][track_id] = "no_message"
                continue
            from okx_signal_system.notify.feishu import send_text

            notification["attempted"] += 1
            if send_text(message):
                notification["sent"] += 1
                notification["tracks"][track_id] = "sent"
                sent_keys[track_id] = key
            else:
                notification["failed"] += 1
                notification["tracks"][track_id] = "feishu_not_sent"
        if notification["sent"]:
            config.notification_state.parent.mkdir(parents=True, exist_ok=True)
            write_text_atomic(
                json.dumps(
                    {
                        "schema": "parallel_acceptance_notification_state_v2",
                        "last_sent_signal_keys": sent_keys,
                        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                config.notification_state,
            )
    else:
        notification["reason"] = "disabled_or_not_requested"
    aggregate["notification"] = notification
    config.output_status.parent.mkdir(parents=True, exist_ok=True)
    write_text_atomic(json.dumps(aggregate, ensure_ascii=False, indent=2), config.output_status)
    return aggregate


__all__ = [
    "AcceptanceTrackConfig",
    "ParallelAcceptanceConfig",
    "VARIANTS",
    "archive_failed_track",
    "build_parallel_acceptance_status",
    "configured_tracks",
    "evaluate_track_admission",
    "evaluate_variant",
    "format_research_shadow_message",
    "latest_research_signal_key",
    "load_parallel_acceptance_config",
    "run_parallel_acceptance",
]
