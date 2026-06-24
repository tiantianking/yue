from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import tomllib
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

import pandas as pd
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from okx_signal_system import __version__
from okx_signal_system.config import env_bool, load_config
from okx_signal_system.research.approved_strategy_manifest import load_approved_manifest_status


@dataclass(frozen=True)
class CheckResult:
    category: str
    name: str
    ok: bool
    detail: str
    blocking: bool = True


FORBIDDEN_RUNTIME_RELEASE_PATHS = (
    "src/okx_signal_system/exchange/position_monitor.py",
    "src/okx_signal_system/notification/",
    "src/okx_signal_system/signal_service/app.py",
    "src/okx_signal_system/ml/",
    "src/okx_signal_system/training/daily_learning.py",
    "src/okx_contract_signal_system.egg-info/",
    "scripts/preflight_check.py",
    "scripts/runtime_healthcheck.py",
    "scripts/check_shadow_ensemble_local.py",
)

FUTURE_LEAK_PATTERNS = (
    ".shift(-",
    "shift(periods=-",
    "lookahead",
    "future_return",
    "forward_return",
    "center=true",
    "center = true",
)

REQUIRED_RESEARCH_CHECKS = {
    "formal_parameter_grid_complete",
    "selected_parameter_symbol_coverage",
    "validation_once_after_freeze",
    "validation_portfolio_passed",
    "finite_pf_and_neighbor_stability_gate",
    "purged_walk_forward_gate",
    "pre_blind_locked",
    "cost_stress_replay_three_scenarios",
    "cost_stress_metrics_passed",
    "near_liq_zero",
    "live_orders_disabled",
}


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key and key not in os.environ:
            os.environ[key] = value


def _writable_directory(path: Path) -> tuple[bool, str]:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".system-check-write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True, str(path)
    except Exception as exc:
        return False, f"{path}: {exc}"


def _explicit_safety_environment() -> tuple[bool, str]:
    expected = {
        "SIGNAL_ONLY": True,
        "DATA_READ_ONLY": True,
        "OKX_AUTO_CLOSE_ENABLED": False,
    }
    problems: list[str] = []
    for name, expected_value in expected.items():
        raw = os.environ.get(name)
        if raw is None:
            problems.append(f"{name}=missing")
            continue
        lowered = raw.strip().lower()
        if lowered not in {"true", "1", "yes", "y", "on", "false", "0", "no", "n", "off"}:
            problems.append(f"{name}=invalid")
            continue
        actual = lowered in {"true", "1", "yes", "y", "on"}
        if actual is not expected_value:
            problems.append(f"{name}={raw}")
    if problems:
        return False, ", ".join(problems)
    return True, "critical safety variables are explicit"


def _private_credentials_empty() -> tuple[bool, str]:
    present = [
        key
        for key in ("OKX_API_KEY", "OKX_SECRET_KEY", "OKX_PASSPHRASE")
        if os.environ.get(key, "").strip()
    ]
    if present:
        return False, "private OKX credentials must be empty in SIGNAL_ONLY deployment: " + ", ".join(present)
    return True, "no private OKX credentials configured"


def configured_symbols() -> list[str]:
    cfg = load_config("base.yaml")
    data = cfg.get("data", {}) if isinstance(cfg, dict) else {}
    symbols = data.get("symbols", []) if isinstance(data, dict) else []
    return [str(item) for item in symbols]


def run_preflight(mode: str, env_file: Path) -> list[CheckResult]:
    _load_env_file(env_file)
    base = load_config("base.yaml")
    execution = base.get("execution", {}) if isinstance(base.get("execution"), dict) else {}
    feishu_cfg = base.get("feishu", {}) if isinstance(base.get("feishu"), dict) else {}

    results: list[CheckResult] = []
    results.append(CheckResult("preflight", "python_version", sys.version_info >= (3, 11), sys.version.split()[0]))
    results.append(CheckResult("preflight", "package_version", bool(__version__), __version__))
    results.append(CheckResult("preflight", "signal_only", env_bool("SIGNAL_ONLY", True), "SIGNAL_ONLY must be true"))
    results.append(CheckResult("preflight", "data_read_only", env_bool("DATA_READ_ONLY", True), "DATA_READ_ONLY must be true"))
    results.append(CheckResult("preflight", "auto_close_disabled", not env_bool("OKX_AUTO_CLOSE_ENABLED", False), "OKX_AUTO_CLOSE_ENABLED must be false"))
    results.append(CheckResult("preflight", "live_order_disabled", execution.get("live_order_enabled") is False, str(execution.get("live_order_enabled"))))
    results.append(CheckResult("preflight", "dry_run_enabled", execution.get("dry_run_enabled") is True, str(execution.get("dry_run_enabled"))))

    explicit_ok, explicit_detail = _explicit_safety_environment()
    results.append(CheckResult("preflight", "explicit_safety_environment", explicit_ok, explicit_detail, blocking=mode == "production"))

    credentials_ok, credentials_detail = _private_credentials_empty()
    results.append(CheckResult("preflight", "private_credentials_empty", credentials_ok, credentials_detail))

    runtime_cache = Path(os.environ.get("JIAOYI_RUNTIME_CACHE_DIR", PROJECT_ROOT / "outputs" / "runtime_cache")).expanduser()
    for name, path in (
        ("outputs_writable", PROJECT_ROOT / "outputs"),
        ("logs_writable", PROJECT_ROOT / "logs"),
        ("runtime_cache_writable", runtime_cache),
    ):
        ok, detail = _writable_directory(path)
        results.append(CheckResult("preflight", name, ok, detail))

    webhook_enabled = env_bool("FEISHU_ENABLED", bool(feishu_cfg.get("enabled", True)))
    webhook_set = bool(os.environ.get("FEISHU_WEBHOOK_URL", "").strip())
    feishu_ok = webhook_enabled and webhook_set if mode == "production" else ((not webhook_enabled) or webhook_set)
    results.append(
        CheckResult(
            "preflight",
            "feishu_configuration",
            feishu_ok,
            "disabled" if not webhook_enabled else ("webhook configured" if webhook_set else "FEISHU_WEBHOOK_URL missing"),
            blocking=mode == "production",
        )
    )

    manifest_status = load_approved_manifest_status()
    results.append(
        CheckResult(
            "preflight",
            "approved_manifest",
            manifest_status.ok,
            manifest_status.reason,
            blocking=mode == "production",
        )
    )
    return results


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _load_outbox_counts(path: Path, *, recent_dead_letter_hours: int = 24) -> dict[str, int]:
    if not path.exists():
        return {}
    try:
        with sqlite3.connect(path) as connection:
            rows = connection.execute("SELECT status, COUNT(*) FROM notification_outbox GROUP BY status").fetchall()
            dead_letter_rows = connection.execute(
                "SELECT updated_at FROM notification_outbox WHERE UPPER(status) = 'DEAD_LETTER'"
            ).fetchall()
    except (sqlite3.Error, OSError):
        return {}
    counts = {str(status).lower(): int(count) for status, count in rows}
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max(1, int(recent_dead_letter_hours)))
    recent_dead_letter = sum(
        1
        for (updated_at,) in dead_letter_rows
        if (parsed := _parse_time(updated_at)) is not None and parsed >= cutoff
    )
    total_dead_letter = counts.get("dead_letter", 0)
    return {
        "pending": counts.get("pending", 0),
        "failed": counts.get("failed", 0),
        "in_progress": counts.get("in_progress", 0),
        "sent": counts.get("sent", 0),
        "dead_letter": recent_dead_letter,
        "dead_letter_total": total_dead_letter,
    }


def _status_symbol_set(status: dict[str, Any]) -> set[str]:
    rows = status.get("symbols")
    if not isinstance(rows, list):
        return set()
    return {
        str(row.get("symbol") or row.get("inst_id"))
        for row in rows
        if isinstance(row, dict) and (row.get("symbol") or row.get("inst_id"))
    }


def _backfill_symbol_set(backfill: dict[str, Any]) -> set[str]:
    rows = backfill.get("symbols")
    if not isinstance(rows, list):
        return set()
    return {
        str(row.get("symbol") or row.get("inst_id"))
        for row in rows
        if isinstance(row, dict) and (row.get("symbol") or row.get("inst_id"))
    }


def evaluate_runtime(
    status: dict[str, Any],
    *,
    mode: str,
    max_age_seconds: int,
    configured: Iterable[str] | None,
    fallback_backfill: dict[str, Any] | None = None,
    authoritative_outbox: dict[str, int] | None = None,
    max_pending: int = 100,
) -> list[CheckResult]:
    now = datetime.now(timezone.utc)
    generated_at = _parse_time(status.get("generated_at"))
    age_seconds = (now - generated_at).total_seconds() if generated_at else float("inf")
    websocket = status.get("websocket") if isinstance(status.get("websocket"), dict) else {}
    modules = status.get("modules") if isinstance(status.get("modules"), dict) else {}
    backfills = status.get("closed_backfills") if isinstance(status.get("closed_backfills"), dict) else {}
    module_backfill = modules.get("closed_kline_backfill") if isinstance(modules.get("closed_kline_backfill"), dict) else {}
    top_level_backfill = status.get("closed_backfill") if isinstance(status.get("closed_backfill"), dict) else {}
    timeframe_backfill = backfills.get("15m") if isinstance(backfills.get("15m"), dict) else {}
    backfill = module_backfill or timeframe_backfill or top_level_backfill or fallback_backfill or {}
    manifest = status.get("manifest_status") if isinstance(status.get("manifest_status"), dict) else {}
    lifecycle = status.get("lifecycle_summary") if isinstance(status.get("lifecycle_summary"), dict) else {}
    status_outbox = lifecycle.get("outbox") if isinstance(lifecycle.get("outbox"), dict) else {}
    outbox = authoritative_outbox or status_outbox

    configured_set = {str(item) for item in configured} if configured is not None else None
    subscription_set = {str(item) for item in websocket.get("subscriptions", []) if item}
    status_set = _status_symbol_set(status)
    coverage_backfill = backfill
    if not _backfill_symbol_set(coverage_backfill) and fallback_backfill:
        coverage_backfill = fallback_backfill
    backfill_set = _backfill_symbol_set(coverage_backfill)
    coverage_detail = (
        f"configured={len(configured_set or set())} status={len(status_set)} "
        f"subscriptions={len(subscription_set)} backfill={len(backfill_set)}"
    )

    websocket_detail = (
        f"connected={websocket.get('connected')} degraded={websocket.get('degraded')} "
        f"reconnect_count={websocket.get('reconnect_count', 0)} last_error={websocket.get('last_error')}"
    )
    backfill_detail = (
        f"all_complete={backfill.get('all_complete')} symbols_checked={backfill.get('symbols_checked')} "
        f"write_failures={backfill.get('write_failures', 0)} latest={backfill.get('expected_latest_closed')}"
    )
    results = [
        CheckResult("runtime", "status_file_fresh", age_seconds <= max_age_seconds, f"age_seconds={age_seconds:.1f}"),
        CheckResult("runtime", "runtime_status", status.get("status") == "running", str(status.get("status"))),
        CheckResult("runtime", "runtime_error", not status.get("error"), str(status.get("error"))),
        CheckResult("runtime", "websocket_connected", websocket.get("connected") is True, websocket_detail),
        CheckResult("runtime", "websocket_not_degraded", websocket.get("degraded") is not True, websocket_detail),
        CheckResult("runtime", "closed_backfill_complete", backfill.get("all_complete") is True, backfill_detail),
        CheckResult("runtime", "outbox_no_failed", int(outbox.get("failed") or 0) == 0, str(outbox.get("failed") or 0)),
        CheckResult(
            "runtime",
            "outbox_no_dead_letter",
            int(outbox.get("dead_letter") or 0) == 0,
            f"recent={int(outbox.get('dead_letter') or 0)} total={int(outbox.get('dead_letter_total', outbox.get('dead_letter', 0)) or 0)} window=24h",
        ),
        CheckResult(
            "runtime",
            "outbox_pending_bounded",
            int(outbox.get("pending") or 0) <= max_pending,
            f"pending={int(outbox.get('pending') or 0)} max={max_pending}",
        ),
    ]
    if configured_set is not None:
        results.extend(
            [
                CheckResult("runtime", "configured_symbol_count", bool(configured_set), f"configured={len(configured_set)}"),
                CheckResult("runtime", "status_symbol_coverage", status_set == configured_set, coverage_detail),
                CheckResult("runtime", "websocket_subscription_coverage", subscription_set == configured_set, coverage_detail),
                CheckResult("runtime", "backfill_symbol_coverage", backfill_set == configured_set, coverage_detail),
            ]
        )
    if mode == "production":
        results.extend(
            [
                CheckResult("runtime", "formal_push_allowed", status.get("push_allowed") is True, str(status.get("push_allowed"))),
                CheckResult("runtime", "approved_manifest_valid", manifest.get("ok") is True, str(manifest.get("reason"))),
            ]
        )
    return results


def run_runtime(
    status_file: Path,
    *,
    mode: str,
    max_age_seconds: int,
    max_pending: int,
) -> list[CheckResult]:
    if not status_file.exists():
        return [CheckResult("runtime", "status_file_exists", False, str(status_file))]
    try:
        status = json.loads(status_file.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        return [CheckResult("runtime", "status_file_valid_json", False, str(exc))]

    fallback_backfill: dict[str, Any] = {}
    fallback_path = status_file.parent / "closed_kline_backfill_status.json"
    if fallback_path.exists():
        try:
            loaded = json.loads(fallback_path.read_text(encoding="utf-8-sig"))
            if isinstance(loaded, dict):
                fallback_backfill = loaded
        except Exception:
            fallback_backfill = {}

    outbox_counts = _load_outbox_counts(status_file.parent / "signal_lifecycle.sqlite3")
    return evaluate_runtime(
        status,
        mode=mode,
        max_age_seconds=max_age_seconds,
        configured=configured_symbols(),
        fallback_backfill=fallback_backfill,
        authoritative_outbox=outbox_counts or None,
        max_pending=max(0, max_pending),
    )


def _read_release_files() -> list[str]:
    path = PROJECT_ROOT / "RELEASE_FILES.txt"
    return [line.strip().replace("\\", "/") for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]


def run_source_audit() -> list[CheckResult]:
    results: list[CheckResult] = []
    pyproject = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project_version = str(pyproject.get("project", {}).get("version", ""))
    results.append(CheckResult("source", "version_consistent", project_version == __version__, f"pyproject={project_version} package={__version__}"))

    release_files = _read_release_files()
    results.append(CheckResult("source", "release_manifest_unique", len(release_files) == len(set(release_files)), f"entries={len(release_files)} unique={len(set(release_files))}"))
    missing = [item for item in release_files if not (PROJECT_ROOT / item).is_file()]
    results.append(CheckResult("source", "release_manifest_files_exist", not missing, ", ".join(missing[:10]) or "all present"))
    forbidden = [
        item
        for item in release_files
        if any(item == prefix or item.startswith(prefix) for prefix in FORBIDDEN_RUNTIME_RELEASE_PATHS)
    ]
    results.append(CheckResult("source", "runtime_release_excludes_obsolete_modules", not forbidden, ", ".join(forbidden) or "clean"))

    symbols = configured_symbols()
    results.append(CheckResult("source", "configured_symbols_unique", len(symbols) == len(set(symbols)), f"count={len(symbols)} unique={len(set(symbols))}"))
    results.append(CheckResult("source", "configured_symbols_are_okx_swaps", bool(symbols) and all(item.endswith("-USDT-SWAP") for item in symbols), f"count={len(symbols)}"))
    results.append(CheckResult("source", "configured_symbol_count_21", len(symbols) == 21, f"count={len(symbols)}"))

    dependencies = {str(item).lower() for item in pyproject.get("project", {}).get("dependencies", [])}
    obsolete_dependencies = sorted(item for item in dependencies if item in {"plotly", "streamlit"})
    results.append(CheckResult("source", "obsolete_python_dependencies_removed", not obsolete_dependencies, ", ".join(obsolete_dependencies) or "clean"))

    env_example = (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8-sig")
    credential_assignments = [
        line
        for line in env_example.splitlines()
        if any(line.startswith(f"{name}=") and line.split("=", 1)[1].strip() for name in ("OKX_API_KEY", "OKX_SECRET_KEY", "OKX_PASSPHRASE"))
    ]
    results.append(CheckResult("source", "env_example_has_no_private_credentials", not credential_assignments, ", ".join(credential_assignments) or "clean"))
    return results


def _json_mapping(path: Path) -> dict[str, Any]:
    loaded = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(loaded, dict):
        raise ValueError(f"expected object: {path}")
    return loaded


def _candidate_code_files(candidate: dict[str, Any], candidate_path: Path) -> list[Path]:
    code_files = candidate.get("code_files", [])
    if not isinstance(code_files, list):
        return []
    out: list[Path] = []
    for raw in code_files:
        path = Path(str(raw))
        if not path.is_absolute():
            path = (candidate_path.parent / path).resolve()
        out.append(path)
    return out


def run_candidate_gate(candidate_path: Path) -> list[CheckResult]:
    try:
        candidate = _json_mapping(candidate_path)
    except Exception as exc:
        return [CheckResult("research", "candidate_json_valid", False, str(exc))]

    mechanism = candidate.get("mechanism") if isinstance(candidate.get("mechanism"), dict) else {}
    data = candidate.get("data") if isinstance(candidate.get("data"), dict) else {}
    freedom = candidate.get("freedom") if isinstance(candidate.get("freedom"), dict) else {}
    duplicate = candidate.get("duplicate") if isinstance(candidate.get("duplicate"), dict) else {}
    leakage = candidate.get("leakage") if isinstance(candidate.get("leakage"), dict) else {}

    results = [
        CheckResult("research", "candidate_id_present", bool(str(candidate.get("candidate_id", "")).strip()), str(candidate.get("candidate_id", ""))),
        CheckResult("research", "payer_identified", bool(str(mechanism.get("payer", "")).strip()), str(mechanism.get("payer", ""))),
        CheckResult("research", "unique_direction_identified", bool(str(mechanism.get("direction", "")).strip()), str(mechanism.get("direction", ""))),
        CheckResult("research", "observable_proxy_identified", bool(str(mechanism.get("observable_proxy", "")).strip()), str(mechanism.get("observable_proxy", ""))),
        CheckResult("research", "same_exchange_okx_only", data.get("exchange") == "OKX" and data.get("cross_exchange") is False, json.dumps(data, ensure_ascii=False)),
        CheckResult("research", "future_returns_closed", leakage.get("future_returns_opened") is False and leakage.get("pnl_opened") is False, json.dumps(leakage, ensure_ascii=False)),
        CheckResult("research", "continuous_parameter_count_bounded", int(freedom.get("continuous_parameters", 999)) <= 3, str(freedom.get("continuous_parameters"))),
        CheckResult("research", "discrete_choice_count_bounded", int(freedom.get("discrete_choices", 999)) <= 4, str(freedom.get("discrete_choices"))),
        CheckResult("research", "duplicate_weight_correlation_bounded", float(duplicate.get("max_abs_weight_correlation", 999.0)) <= 0.50, str(duplicate.get("max_abs_weight_correlation"))),
        CheckResult("research", "duplicate_same_side_overlap_bounded", float(duplicate.get("max_same_side_overlap", 999.0)) <= 0.25, str(duplicate.get("max_same_side_overlap"))),
        CheckResult("research", "representation_invariance_passed", candidate.get("representation_invariance_passed") is True, str(candidate.get("representation_invariance_passed"))),
        CheckResult("research", "measurement_semantics_passed", candidate.get("measurement_semantics_passed") is True, str(candidate.get("measurement_semantics_passed"))),
    ]

    code_files = _candidate_code_files(candidate, candidate_path)
    missing = [str(path) for path in code_files if not path.is_file()]
    results.append(CheckResult("research", "candidate_code_files_exist", not missing, ", ".join(missing) or f"count={len(code_files)}"))
    hits: list[str] = []
    for path in code_files:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace").lower()
        for pattern in FUTURE_LEAK_PATTERNS:
            if pattern in text:
                hits.append(f"{path.name}:{pattern}")
    results.append(CheckResult("research", "static_future_leak_scan", not hits, ", ".join(hits) or "clean"))
    return results


def _bool_series(series: pd.Series) -> pd.Series:
    if series.dtype == bool:
        return series
    return series.astype(str).str.strip().str.lower().isin({"true", "1", "yes", "passed"})


def _positive_contribution_share(frame: pd.DataFrame, group_column: str) -> float:
    if frame.empty or group_column not in frame or "net_r" not in frame:
        return 1.0
    grouped = pd.to_numeric(frame["net_r"], errors="coerce").fillna(0.0).groupby(frame[group_column]).sum()
    positive = grouped[grouped > 0.0]
    total = float(positive.sum())
    if total <= 0.0:
        return 1.0
    return float(positive.max() / total)


def run_artifact_gate(artifact_dir: Path) -> list[CheckResult]:
    results: list[CheckResult] = []
    required = {
        "acceptance_checklist.csv",
        "cost_stress.csv",
        "sample_trades.csv",
        "portfolio_results.csv",
        "candidate_params.json",
    }
    missing = sorted(name for name in required if not (artifact_dir / name).is_file())
    results.append(CheckResult("research", "required_artifacts_present", not missing, ", ".join(missing) or "complete"))
    if missing:
        return results

    checklist = pd.read_csv(artifact_dir / "acceptance_checklist.csv")
    checks_present = set(checklist.get("check", pd.Series(dtype=str)).astype(str))
    passed = _bool_series(checklist.get("passed", pd.Series(dtype=bool)))
    failed_names = checklist.loc[~passed, "check"].astype(str).tolist() if "check" in checklist else ["invalid_checklist"]
    results.append(CheckResult("research", "acceptance_checklist_all_passed", bool(len(checklist)) and bool(passed.all()), ", ".join(failed_names) or "all passed"))
    missing_checks = sorted(REQUIRED_RESEARCH_CHECKS - checks_present)
    results.append(CheckResult("research", "required_acceptance_checks_present", not missing_checks, ", ".join(missing_checks) or "complete"))

    stress = pd.read_csv(artifact_dir / "cost_stress.csv")
    scenarios = set(stress.get("scenario", pd.Series(dtype=str)).astype(str))
    results.append(CheckResult("research", "cost_stress_scenarios_complete", {"baseline", "stress_1_5x", "stress_2x"}.issubset(scenarios), ", ".join(sorted(scenarios))))

    trades = pd.read_csv(artifact_dir / "sample_trades.csv")
    symbol_share = _positive_contribution_share(trades, "inst_id")
    if "exit_time" in trades:
        parsed = pd.to_datetime(trades["exit_time"], utc=True, errors="coerce")
        trades = trades.assign(_month=parsed.dt.strftime("%Y-%m"))
    month_share = _positive_contribution_share(trades, "_month")
    net_r = pd.to_numeric(trades.get("net_r", pd.Series(dtype=float)), errors="coerce").fillna(0.0)
    positive_total = float(net_r[net_r > 0.0].sum())
    top_trade_share = float(net_r.max() / positive_total) if positive_total > 0.0 and not net_r.empty else 1.0
    results.extend(
        [
            CheckResult("research", "single_symbol_positive_contribution_bounded", symbol_share <= 0.25, f"share={symbol_share:.4f}"),
            CheckResult("research", "single_month_positive_contribution_bounded", month_share <= 0.35, f"share={month_share:.4f}"),
            CheckResult("research", "single_trade_positive_contribution_bounded", top_trade_share <= 0.25, f"share={top_trade_share:.4f}"),
        ]
    )

    candidate_payload = _json_mapping(artifact_dir / "candidate_params.json")
    results.append(CheckResult("research", "formal_candidate_type", candidate_payload.get("artifact_type") == "strict_research_candidate", str(candidate_payload.get("artifact_type"))))
    results.append(CheckResult("research", "promotion_requires_manual_gate", candidate_payload.get("promotion_eligible") is True, str(candidate_payload.get("promotion_eligible"))))
    return results


def archive_failed_research(
    candidate_path: Path,
    artifact_dir: Path | None,
    archive_root: Path,
    results: list[CheckResult],
) -> Path:
    candidate_id = "unknown_candidate"
    try:
        candidate_id = str(_json_mapping(candidate_path).get("candidate_id") or candidate_id)
    except Exception:
        pass
    safe_id = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in candidate_id)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination = archive_root / f"{safe_id}_{timestamp}"
    destination.mkdir(parents=True, exist_ok=False)
    shutil.copy2(candidate_path, destination / candidate_path.name)
    if artifact_dir and artifact_dir.is_dir():
        for name in (
            "acceptance_checklist.csv",
            "cost_stress.csv",
            "portfolio_results.csv",
            "candidate_params.json",
            "final_report.md",
        ):
            source = artifact_dir / name
            if source.is_file():
                shutil.copy2(source, destination / name)
    summary = {
        "status": "REJECT_AND_ARCHIVE_NO_RESCUE",
        "candidate_id": candidate_id,
        "archived_at": datetime.now(timezone.utc).isoformat(),
        "failed_checks": [asdict(item) for item in results if item.blocking and not item.ok],
    }
    (destination / "failure_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return destination


async def run_shadow_check(*, write_runtime_output: bool = False) -> list[CheckResult]:
    from okx_signal_system.shadow_ensemble import ShadowEnsembleService, ShadowEnsembleStore, load_shadow_ensemble_config

    base = load_config("base.yaml")
    symbols = [str(item) for item in base.get("data", {}).get("symbols", [])]
    cache_dir = PROJECT_ROOT / "outputs" / "runtime_cache" / "lightweight_history" / "okx_15m_extended"
    if not cache_dir.is_dir():
        return [CheckResult("shadow", "runtime_cache_exists", False, str(cache_dir), blocking=False)]

    config = load_shadow_ensemble_config()

    def runtime_filename(symbol: str) -> str:
        normalized = symbol.replace("-", "_").replace("_SWAP", "").upper()
        if normalized.count("USDT") == 1:
            normalized = f"{normalized}_USDT"
        return f"{normalized}_15m.parquet"

    async def loader(symbol: str, limit: int) -> pd.DataFrame:
        path = cache_dir / runtime_filename(symbol)
        if not path.is_file():
            raise FileNotFoundError(path)
        frame = await asyncio.to_thread(pd.read_parquet, path)
        return frame.tail(limit).reset_index(drop=True)

    if write_runtime_output:
        service = ShadowEnsembleService(candle_loader=loader, config=config)
        scan = await service.scan(symbols)
    else:
        with tempfile.TemporaryDirectory(prefix="okx-shadow-check-") as temp:
            temp_dir = Path(temp)
            isolated = replace(config, status_file=str(temp_dir / "shadow_status.json"), sqlite_file=str(temp_dir / "shadow.sqlite3"))
            service = ShadowEnsembleService(candle_loader=loader, config=isolated, store=ShadowEnsembleStore(temp_dir / "shadow.sqlite3"))
            scan = await service.scan(symbols)

    return [
        CheckResult("shadow", "shadow_status_running", scan.status == "running", scan.status),
        CheckResult("shadow", "shadow_symbol_coverage", int(scan.eligible_symbols) == len(symbols), f"eligible={scan.eligible_symbols} configured={len(symbols)} skipped={list(scan.skipped_symbols)}"),
    ]


def _print_results(results: list[CheckResult], *, json_output: bool) -> int:
    failed = [item for item in results if item.blocking and not item.ok]
    if json_output:
        print(json.dumps({"ok": not failed, "version": __version__, "checks": [asdict(item) for item in results]}, ensure_ascii=False, indent=2))
    else:
        for item in results:
            state = "PASS" if item.ok else ("FAIL" if item.blocking else "WARN")
            print(f"[{state}] {item.category}.{item.name}: {item.detail}")
        print("SYSTEM CHECK PASSED" if not failed else "SYSTEM CHECK FAILED")
    return 0 if not failed else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Unified source, deployment, runtime, shadow, and research checks for the OKX signal-only system.")
    parser.add_argument("command", choices=("source", "preflight", "runtime", "shadow", "research", "all"), nargs="?", default="all")
    parser.add_argument("--mode", choices=("observation", "production"), default=os.environ.get("DEPLOYMENT_MODE", "observation"))
    parser.add_argument("--env-file", type=Path, default=PROJECT_ROOT / ".env")
    parser.add_argument("--status-file", type=Path, default=PROJECT_ROOT / "outputs" / "latest_scan_status.json")
    parser.add_argument("--max-age-seconds", type=int, default=int(os.environ.get("HEALTH_MAX_AGE_SECONDS", "1200")))
    parser.add_argument("--max-pending", type=int, default=int(os.environ.get("OUTBOX_MAX_PENDING", "100")))
    parser.add_argument("--candidate", type=Path)
    parser.add_argument("--artifacts", type=Path)
    parser.add_argument("--archive-failures", type=Path)
    parser.add_argument("--write-shadow-output", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    results: list[CheckResult] = []
    if args.command in {"source", "all"}:
        results.extend(run_source_audit())
    if args.command in {"preflight", "all"}:
        results.extend(run_preflight(args.mode, args.env_file))
    if args.command in {"runtime", "all"}:
        results.extend(run_runtime(args.status_file, mode=args.mode, max_age_seconds=args.max_age_seconds, max_pending=args.max_pending))
    if args.command in {"shadow", "all"}:
        results.extend(asyncio.run(run_shadow_check(write_runtime_output=args.write_shadow_output)))
    if args.command == "research":
        if args.candidate is None:
            parser.error("research command requires --candidate")
        results.extend(run_candidate_gate(args.candidate))
        if args.artifacts is not None:
            results.extend(run_artifact_gate(args.artifacts))
        if args.archive_failures and any(item.blocking and not item.ok for item in results):
            destination = archive_failed_research(args.candidate, args.artifacts, args.archive_failures, results)
            results.append(CheckResult("research", "failure_archived", True, str(destination), blocking=False))

    return _print_results(results, json_output=args.json)


if __name__ == "__main__":
    raise SystemExit(main())
