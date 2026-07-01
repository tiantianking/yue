from __future__ import annotations

import argparse
import ast
import asyncio
import hashlib
import json
import math
import os
import re
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

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from okx_signal_system import __version__
from okx_signal_system.config import env_bool, load_config
from okx_signal_system.research.approved_strategy_manifest import load_approved_manifest_status
from okx_signal_system.research.robustness_screen import (
    evaluate_robustness_screen,
    frozen_protocol_ok,
)


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
    ".pct_change(-",
    ".diff(-",
    "center=true",
    "center = true",
    ".bfill(",
    ".backfill(",
    "method=\"bfill\"",
    "method='bfill'",
    "direction=\"forward\"",
    "direction='forward'",
    "direction=\"nearest\"",
    "direction='nearest'",
)

MIN_HOLDOUT_MONTHS = 6
DEFAULT_HOLDOUT_MONTHS = 8
MAX_HOLDOUT_MONTHS = 10
MIN_HOLDOUT_DAYS = 170
MAX_HOLDOUT_DAYS = 320
SHA256_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")

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

MAX_FREE_PARAMETERS = 4
MAX_PARAMETER_COMBINATIONS = 216
MAX_FAMILY_SIMILARITY = 0.72
MIN_FAILURE_FINGERPRINT_TAG_MATCHES = 2
MIN_FAILURE_FINGERPRINT_COVERAGE = 0.25
FINGERPRINT_GENERIC_TAGS = {
    "asset",
    "assets",
    "cross_sectional",
    "factor",
    "market",
    "okx",
    "portfolio",
    "price",
    "rank",
    "return",
    "returns",
    "signal",
    "strategy",
    "swap",
    "symbol",
    "symbols",
    "time_series",
    "usdt",
}
MAX_SYMBOL_CONTRIBUTION = 0.25
MIN_SYMBOLS_FOR_CROSS_SYMBOL_CONTRIBUTION_GATE = 6
MAX_MONTH_CONTRIBUTION = 0.35
MAX_SINGLE_TRADE_CONTRIBUTION = 0.25
MAX_TOP_THREE_TRADE_CONTRIBUTION = 0.50
MIN_EFFECTIVE_POSITIVE_TRADES = 10.0
MIN_VALIDATION_TRADES = 80
DEFAULT_MIN_RESEARCH_SYMBOLS = 12
DEFAULT_MIN_HISTORY_DAYS = 365.0
DEFAULT_MIN_NEW_DATA_DAYS = 30.0
DEFAULT_MAX_GAP_RATIO = 0.02
DEFAULT_DATA_COVERAGE_RATIO = 0.80
DEFAULT_FAMILY_REGISTRY = PROJECT_ROOT / "config" / "research_family_registry.json"
DEFAULT_UNIVERSE_POLICY = PROJECT_ROOT / "config" / "research_universe_policy.json"
DEFAULT_DATA_STATE = PROJECT_ROOT / "outputs" / "research_data_state.json"
DEFAULT_DATA_REPORT = PROJECT_ROOT / "outputs" / "research_data_readiness.json"
DEFAULT_RESEARCH_REPORT = PROJECT_ROOT / "outputs" / "research_gate_report.json"


@dataclass(frozen=True)
class ParameterAudit:
    free_parameters: int
    combinations: int
    bounded: bool
    names: tuple[str, ...]
    problems: tuple[str, ...]


@dataclass(frozen=True)
class DataReadiness:
    dataset: str
    timeframe: str
    ready: bool
    initial_research: bool
    symbol_count: int
    covered_symbols: int
    history_qualified_symbols: int
    new_data_qualified_symbols: int
    required_new_bars: int
    latest_closed_by_symbol: dict[str, str]
    rows: tuple[dict[str, Any], ...]


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
    configured = configured_symbols()
    results = evaluate_runtime(
        status,
        mode=mode,
        max_age_seconds=max_age_seconds,
        configured=configured,
        fallback_backfill=fallback_backfill,
        authoritative_outbox=outbox_counts or None,
        max_pending=max(0, max_pending),
    )

    dashboard_5m_path = status_file.parent / "closed_kline_backfill_status_5m.json"
    if not dashboard_5m_path.exists():
        results.append(
            CheckResult(
                "runtime",
                "dashboard_5m_backfill_status_exists",
                False,
                str(dashboard_5m_path),
                blocking=False,
            )
        )
        return results

    try:
        dashboard_5m = json.loads(dashboard_5m_path.read_text(encoding="utf-8-sig"))
        generated_5m = _parse_time(dashboard_5m.get("generated_at"))
        age_5m = (
            (datetime.now(timezone.utc) - generated_5m).total_seconds()
            if generated_5m
            else float("inf")
        )
        maximum_5m_age = max(900, max_age_seconds)
        dashboard_5m_symbols = _backfill_symbol_set(dashboard_5m)
        configured_set = {str(item) for item in configured}
        detail_5m = (
            f"age_seconds={age_5m:.1f} all_complete={dashboard_5m.get('all_complete')} "
            f"symbols_checked={dashboard_5m.get('symbols_checked')} "
            f"write_failures={dashboard_5m.get('write_failures', 0)} "
            f"latest={dashboard_5m.get('expected_latest_closed')}"
        )
        results.extend(
            [
                CheckResult(
                    "runtime",
                    "dashboard_5m_backfill_fresh",
                    age_5m <= maximum_5m_age,
                    f"{detail_5m} max_age_seconds={maximum_5m_age}",
                    blocking=False,
                ),
                CheckResult(
                    "runtime",
                    "dashboard_5m_backfill_complete",
                    dashboard_5m.get("all_complete") is True
                    and int(dashboard_5m.get("write_failures") or 0) == 0,
                    detail_5m,
                    blocking=False,
                ),
                CheckResult(
                    "runtime",
                    "dashboard_5m_symbol_coverage",
                    dashboard_5m_symbols == configured_set,
                    f"configured={len(configured_set)} backfill={len(dashboard_5m_symbols)}",
                    blocking=False,
                ),
            ]
        )
    except Exception as exc:
        results.append(
            CheckResult(
                "runtime",
                "dashboard_5m_backfill_status_valid",
                False,
                str(exc),
                blocking=False,
            )
        )
    return results


def _read_release_files() -> list[str]:
    path = PROJECT_ROOT / "RELEASE_FILES.txt"
    return [line.strip().replace("\\", "/") for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]


def run_source_audit() -> list[CheckResult]:
    results: list[CheckResult] = []
    pyproject = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project_version = str(pyproject.get("project", {}).get("version", ""))
    results.append(CheckResult("source", "version_consistent", project_version == __version__, f"pyproject={project_version} package={__version__}"))

    overview_path = PROJECT_ROOT / "docs" / "PROJECT_OVERVIEW_CN.md"
    policy_path = PROJECT_ROOT / "docs" / "CHANGE_CONTROL_POLICY_CN.md"
    release_note_path = PROJECT_ROOT / "docs" / f"V{__version__}_RELEASE_CN.md"
    overview_text = overview_path.read_text(encoding="utf-8") if overview_path.is_file() else ""
    results.extend(
        [
            CheckResult(
                "source",
                "project_overview_current",
                overview_path.is_file() and f"当前版本：v{__version__}" in overview_text,
                str(overview_path),
            ),
            CheckResult("source", "change_control_policy_present", policy_path.is_file(), str(policy_path)),
            CheckResult("source", "current_release_note_present", release_note_path.is_file(), str(release_note_path)),
        ]
    )

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

    template_path = PROJECT_ROOT / "config" / "research_candidates" / "PRE_PNL_CANDIDATE_TEMPLATE.json"
    registry_path = DEFAULT_FAMILY_REGISTRY
    try:
        template = _json_mapping(template_path)
        template_data = template.get("data") if isinstance(template.get("data"), dict) else {}
        template_gate = template.get("data_gate") if isinstance(template.get("data_gate"), dict) else {}
        template_selection = (
            template.get("universe_selection")
            if isinstance(template.get("universe_selection"), dict)
            else {}
        )
        template_holdout = template.get("historical_holdout") if isinstance(template.get("historical_holdout"), dict) else {}
        template_trial_ledger = template.get("trial_ledger") if isinstance(template.get("trial_ledger"), dict) else {}
        template_point_in_time = template.get("point_in_time") if isinstance(template.get("point_in_time"), dict) else {}
        template_outcome_horizon = template.get("outcome_horizon") if isinstance(template.get("outcome_horizon"), dict) else {}
        template_dependency_manifest = template.get("code_dependency_manifest") if isinstance(template.get("code_dependency_manifest"), dict) else {}
        template_ok = bool(
            template.get("schema") == "okx_pre_pnl_candidate_v2"
            and template_gate.get("min_symbols") == 1
            and float(template_gate.get("coverage_ratio", 0.0)) == 1.0
            and isinstance(template_data.get("symbols"), list)
            and template_selection.get("selection_locked_before_pnl") is True
            and template_selection.get("outcome_based_selection") is False
            and template_holdout.get("locked_before_pnl") is True
            and template_holdout.get("months") == DEFAULT_HOLDOUT_MONTHS
            and template_holdout.get("opened_count") == 0
            and template_trial_ledger.get("all_family_trials_recorded") is True
            and template_point_in_time.get("all_fields_have_available_at") is True
            and template_outcome_horizon.get("locked_before_pnl") is True
            and template_dependency_manifest.get("complete") is True
        )
        template_detail = (
            f"schema={template.get('schema')} min_symbols={template_gate.get('min_symbols')} "
            f"coverage={template_gate.get('coverage_ratio')} holdout_months={template_holdout.get('months')} "
            f"trial_ledger={template_trial_ledger.get('all_family_trials_recorded')} "
            f"point_in_time={template_point_in_time.get('all_fields_have_available_at')}"
        )
    except Exception as exc:
        template_ok = False
        template_detail = str(exc)
    try:
        registry = _json_mapping(registry_path)
        registry_families = registry.get("families", [])
        registry_fingerprints = registry.get("failure_fingerprints", [])
        registry_ok = (
            registry.get("schema") == "okx_research_family_registry_v1"
            and isinstance(registry_families, list)
            and len(registry_families) >= 3
            and isinstance(registry_fingerprints, list)
            and len(registry_fingerprints) >= 30
        )
        registry_detail = (
            f"schema={registry.get('schema')} "
            f"families={len(registry_families) if isinstance(registry_families, list) else 0} "
            f"fingerprints={len(registry_fingerprints) if isinstance(registry_fingerprints, list) else 0}"
        )
    except Exception as exc:
        registry_ok = False
        registry_detail = str(exc)
    results.append(CheckResult("source", "research_candidate_template_v2", template_ok, template_detail))
    results.append(CheckResult("source", "research_family_registry_valid", registry_ok, registry_detail))
    results.append(CheckResult("source", "unified_research_gate_released", "scripts/system_check.py" in release_files, "scripts/system_check.py"))
    results.append(CheckResult("source", "research_registry_released", "config/research_family_registry.json" in release_files, "config/research_family_registry.json"))
    parallel_release_files = {
        "RUN_PARALLEL_ACCEPTANCE.cmd",
        "config/parallel_acceptance.yaml",
        "config/parallel_acceptance_early_stop_protocol.json",
        "config/shadow_ensemble_forward_acceptance_protocol.json",
        "docs/PARALLEL_FORWARD_ACCEPTANCE_CN.md",
        "scripts/run_candidate_factory.py",
        "scripts/run_parallel_acceptance.py",
        "scripts/update_shadow_ensemble_acceptance.py",
        "src/okx_signal_system/research/parallel_acceptance.py",
        "src/okx_signal_system/research/shadow_ensemble_acceptance.py",
    }
    missing_parallel_release = sorted(parallel_release_files.difference(release_files))
    results.append(
        CheckResult(
            "source",
            "parallel_research_pipeline_released",
            not missing_parallel_release,
            ", ".join(missing_parallel_release) or "complete",
        )
    )
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


def _candidate_relative_path(raw: Any, candidate_path: Path) -> Path | None:
    text = str(raw or "").strip()
    if not text:
        return None
    path = Path(text)
    if not path.is_absolute():
        path = (candidate_path.parent / path).resolve()
    return path


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _valid_sha256(value: Any) -> bool:
    return bool(SHA256_PATTERN.fullmatch(str(value or "").strip()))


def _hashed_evidence_status(raw_path: Any, declared_hash: Any, candidate_path: Path) -> tuple[bool, str, Path | None]:
    path = _candidate_relative_path(raw_path, candidate_path)
    if path is None:
        return False, "missing_path", None
    if not path.is_file():
        return False, f"missing_file:{path}", path
    declared = str(declared_hash or "").strip().lower()
    if not _valid_sha256(declared):
        return False, f"invalid_sha256:{declared}", path
    actual = _file_sha256(path)
    return actual == declared, f"path={path} declared={declared} actual={actual}", path


def _parse_utc_timestamp(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _literal_number(node: ast.AST | None) -> float | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return float(node.value)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        value = _literal_number(node.operand)
        return -value if value is not None else None
    return None


def _call_name(node: ast.Call) -> str:
    if isinstance(node.func, ast.Attribute):
        return node.func.attr.lower()
    if isinstance(node.func, ast.Name):
        return node.func.id.lower()
    return ""


class _FutureLeakVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.hits: list[str] = []
        self.numeric_names: dict[str, float] = {}

    def _hit(self, node: ast.AST, reason: str) -> None:
        self.hits.append(f"line={getattr(node, 'lineno', '?')}:{reason}")

    def _resolved_number(self, node: ast.AST | None) -> float | None:
        literal = _literal_number(node)
        if literal is not None:
            return literal
        if isinstance(node, ast.Name):
            return self.numeric_names.get(node.id)
        return None

    def visit_Assign(self, node: ast.Assign) -> None:
        value = _literal_number(node.value)
        if value is not None:
            for target in node.targets:
                if isinstance(target, ast.Name):
                    self.numeric_names[target.id] = value
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        value = _literal_number(node.value)
        if value is not None and isinstance(node.target, ast.Name):
            self.numeric_names[node.target.id] = value
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        name = _call_name(node)
        if name in {"shift", "pct_change", "diff"}:
            periods_node = node.args[0] if node.args else None
            for keyword in node.keywords:
                if keyword.arg in {"periods", "period"}:
                    periods_node = keyword.value
            periods = self._resolved_number(periods_node)
            if periods is not None and periods < 0:
                self._hit(node, f"negative_{name}({periods:g})")
        if name == "rolling":
            for keyword in node.keywords:
                if keyword.arg == "center" and isinstance(keyword.value, ast.Constant) and keyword.value.value is True:
                    self._hit(node, "centered_rolling_window")
        if name in {"bfill", "backfill"}:
            self._hit(node, f"future_fill:{name}")
        if name == "fillna":
            for keyword in node.keywords:
                if keyword.arg == "method" and isinstance(keyword.value, ast.Constant):
                    method = str(keyword.value.value).strip().lower()
                    if method in {"bfill", "backfill"}:
                        self._hit(node, f"future_fill:fillna_{method}")
        if name == "merge_asof":
            for keyword in node.keywords:
                if keyword.arg == "direction" and isinstance(keyword.value, ast.Constant):
                    direction = str(keyword.value.value).strip().lower()
                    if direction in {"forward", "nearest"}:
                        self._hit(node, f"merge_asof_future_direction:{direction}")
        if name == "interpolate":
            for keyword in node.keywords:
                if keyword.arg == "limit_direction" and isinstance(keyword.value, ast.Constant):
                    direction = str(keyword.value.value).strip().lower()
                    if direction in {"backward", "both"}:
                        self._hit(node, f"future_interpolation:{direction}")
        if name in {"lead", "future", "lookahead"}:
            self._hit(node, f"suspicious_call:{name}")
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        value = node.value
        is_iloc = isinstance(value, ast.Attribute) and value.attr == "iloc"
        index = node.slice
        if is_iloc and isinstance(index, ast.BinOp) and isinstance(index.op, ast.Add):
            increment = _literal_number(index.right)
            if increment is not None and increment > 0:
                self._hit(node, f"forward_iloc_offset(+{increment:g})")
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, str):
            lowered = node.value.strip().lower()
            if re.search(r"(?:future|forward|next)_(?:return|ret|pnl|price|close|label)", lowered):
                self._hit(node, f"future_label_reference:{lowered[:80]}")
        self.generic_visit(node)


def scan_future_leaks(path: Path) -> tuple[bool, list[str]]:
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source, filename=str(path))
    except (OSError, SyntaxError) as exc:
        return False, [f"parse_error:{exc}"]
    visitor = _FutureLeakVisitor()
    visitor.visit(tree)
    lowered = source.lower()
    for pattern in FUTURE_LEAK_PATTERNS:
        if pattern in lowered and not any(pattern in hit for hit in visitor.hits):
            visitor.hits.append(f"text_pattern:{pattern}")
    return True, sorted(set(visitor.hits))


def _trial_ledger_status(path: Path | None, candidate_id: str, declared_count: Any) -> tuple[bool, str]:
    if path is None or not path.is_file():
        return False, f"missing_trial_ledger:{path}"
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        return False, f"invalid_trial_ledger:{exc}"
    if not isinstance(payload, dict):
        return False, "trial_ledger_not_object"
    trials = payload.get("trials")
    if not isinstance(trials, list):
        return False, "trial_ledger_trials_missing"
    ids = [str(item.get("candidate_id", "")).strip() for item in trials if isinstance(item, dict)]
    try:
        expected = int(declared_count)
    except (TypeError, ValueError):
        return False, f"invalid_declared_trial_count:{declared_count}"
    all_registered_before_pnl = all(
        isinstance(item, dict) and item.get("registered_before_pnl") is True
        for item in trials
    )
    ok = bool(
        payload.get("schema") == "okx_family_trial_ledger_v1"
        and payload.get("complete") is True
        and expected == len(trials)
        and candidate_id in ids
        and all(bool(item) for item in ids)
        and all_registered_before_pnl
    )
    return ok, f"schema={payload.get('schema')} complete={payload.get('complete')} declared={expected} actual={len(trials)} candidate_present={candidate_id in ids} all_registered_before_pnl={all_registered_before_pnl}"


def _point_in_time_evidence_status(path: Path | None, required_fields: list[str]) -> tuple[bool, str]:
    if path is None or not path.is_file():
        return False, f"missing_point_in_time_evidence:{path}"
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        return False, f"invalid_point_in_time_evidence:{exc}"
    if not isinstance(payload, dict):
        return False, "point_in_time_evidence_not_object"
    fields = payload.get("fields")
    if not isinstance(fields, list):
        return False, "point_in_time_fields_missing"
    valid_policies = {"non_revising", "versioned", "frozen_snapshot"}
    rows = [item for item in fields if isinstance(item, dict)]
    names = {str(item.get("name", "")).strip() for item in rows if str(item.get("name", "")).strip()}
    row_quality = all(
        _meaningful_text(item.get("available_at_rule"))
        and str(item.get("revision_policy", "")).strip() in valid_policies
        for item in rows
    )
    required = {str(item).strip() for item in required_fields if str(item).strip()}
    ok = bool(
        payload.get("schema") == "okx_point_in_time_evidence_v1"
        and payload.get("complete") is True
        and required
        and required.issubset(names)
        and row_quality
    )
    return ok, f"schema={payload.get('schema')} complete={payload.get('complete')} required={sorted(required)} covered={sorted(names)} row_quality={row_quality}"


def _dependency_manifest_status(path: Path | None, code_files: list[Path]) -> tuple[bool, str]:
    if path is None or not path.is_file():
        return False, f"missing_dependency_manifest:{path}"
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        return False, f"invalid_dependency_manifest:{exc}"
    if not isinstance(payload, dict):
        return False, "dependency_manifest_not_object"
    raw_files = payload.get("files")
    if not isinstance(raw_files, list):
        return False, "dependency_manifest_files_missing"
    declared: set[Path] = set()
    for raw in raw_files:
        item = Path(str(raw))
        if not item.is_absolute():
            item = (path.parent / item).resolve()
        declared.add(item)
    expected = {item.resolve() for item in code_files}
    missing = sorted(str(item) for item in expected.difference(declared))
    nonexistent = sorted(str(item) for item in declared if not item.is_file())
    ok = bool(
        payload.get("schema") == "okx_code_dependency_manifest_v1"
        and payload.get("complete") is True
        and expected
        and not missing
        and not nonexistent
    )
    return ok, f"schema={payload.get('schema')} complete={payload.get('complete')} expected={len(expected)} declared={len(declared)} missing={missing} nonexistent={nonexistent}"


def _holdout_manifest_status(
    path: Path | None,
    *,
    candidate_id: str,
    months: Any,
    start_utc: Any,
    end_utc: Any,
    data_snapshot_sha256: Any,
    split_sha256: Any,
) -> tuple[bool, str]:
    if path is None or not path.is_file():
        return False, f"missing_holdout_manifest:{path}"
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        return False, f"invalid_holdout_manifest:{exc}"
    if not isinstance(payload, dict):
        return False, "holdout_manifest_not_object"
    ok = bool(
        payload.get("schema") == "okx_historical_holdout_manifest_v1"
        and payload.get("candidate_id") == candidate_id
        and payload.get("locked_before_pnl") is True
        and payload.get("opened_count") == 0
        and payload.get("months") == months
        and payload.get("start_utc") == start_utc
        and payload.get("end_utc") == end_utc
        and payload.get("data_snapshot_sha256") == data_snapshot_sha256
        and payload.get("split_sha256") == split_sha256
    )
    return ok, f"schema={payload.get('schema')} candidate={payload.get('candidate_id')} locked={payload.get('locked_before_pnl')} opened_count={payload.get('opened_count')} months={payload.get('months')} start={payload.get('start_utc')} end={payload.get('end_utc')} data_snapshot_match={payload.get('data_snapshot_sha256') == data_snapshot_sha256} split_match={payload.get('split_sha256') == split_sha256}"


def _parameter_choice_count(parameter: dict[str, Any]) -> tuple[int, str | None]:
    if parameter.get("tuned") is False:
        return 1, None
    values = parameter.get("values")
    if isinstance(values, list):
        unique = {json.dumps(value, sort_keys=True, ensure_ascii=False) for value in values}
        if not unique:
            return 0, "empty_values"
        return len(unique), None
    bounds = parameter.get("range")
    if isinstance(bounds, dict):
        try:
            minimum = float(bounds["min"])
            maximum = float(bounds["max"])
            step = float(bounds["step"])
        except (KeyError, TypeError, ValueError):
            return 0, "range_requires_numeric_min_max_step"
        if not math.isfinite(minimum) or not math.isfinite(maximum) or not math.isfinite(step) or step <= 0 or maximum < minimum:
            return 0, "invalid_range"
        count = int(math.floor((maximum - minimum) / step + 1e-12)) + 1
        return count, None if count > 0 else "empty_range"
    if "value" in parameter:
        return 1, None
    return 0, "missing_values_range_or_value"


def audit_parameter_space(candidate: dict[str, Any]) -> ParameterAudit:
    space = candidate.get("parameter_space") if isinstance(candidate.get("parameter_space"), dict) else {}
    parameters = space.get("parameters") if isinstance(space.get("parameters"), list) else []
    free_parameters = 0
    combinations = 1
    names: list[str] = []
    problems: list[str] = []
    for index, raw in enumerate(parameters):
        if not isinstance(raw, dict):
            problems.append(f"parameter_{index}:not_object")
            continue
        name = str(raw.get("name", "")).strip()
        if not name:
            problems.append(f"parameter_{index}:missing_name")
            name = f"parameter_{index}"
        if name in names:
            problems.append(f"duplicate_parameter:{name}")
        names.append(name)
        choices, problem = _parameter_choice_count(raw)
        if problem:
            problems.append(f"{name}:{problem}")
            continue
        if choices > 1:
            free_parameters += 1
        combinations *= max(1, choices)
        if combinations > MAX_PARAMETER_COMBINATIONS:
            problems.append(f"grid_too_large:{combinations}>{MAX_PARAMETER_COMBINATIONS}")
    if space.get("all_choices_declared_before_pnl") is not True:
        problems.append("choices_not_locked_before_pnl")
    if free_parameters > MAX_FREE_PARAMETERS:
        problems.append(f"too_many_free_parameters:{free_parameters}>{MAX_FREE_PARAMETERS}")
    declared_free = space.get("declared_free_parameters")
    if declared_free is not None:
        try:
            declared_free_value = int(declared_free)
        except (TypeError, ValueError):
            problems.append(f"declared_free_invalid:{declared_free}")
        else:
            if declared_free_value != free_parameters:
                problems.append(f"declared_free_mismatch:{declared_free}!={free_parameters}")
    declared_combinations = space.get("declared_combinations")
    if declared_combinations is not None:
        try:
            declared_combinations_value = int(declared_combinations)
        except (TypeError, ValueError):
            problems.append(f"declared_combinations_invalid:{declared_combinations}")
        else:
            if declared_combinations_value != combinations:
                problems.append(f"declared_combinations_mismatch:{declared_combinations}!={combinations}")
    return ParameterAudit(
        free_parameters=free_parameters,
        combinations=combinations,
        bounded=not problems,
        names=tuple(names),
        problems=tuple(problems),
    )


def _meaningful_text(value: Any) -> bool:
    text = " ".join(str(value or "").strip().lower().split())
    return bool(text) and text not in {"replace_me", "todo", "tbd"}


def _normalise_family_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _normalise_family_value(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return sorted((_normalise_family_value(item) for item in value), key=lambda item: json.dumps(item, sort_keys=True, ensure_ascii=False))
    if isinstance(value, str):
        return " ".join(value.strip().lower().split())
    return value


def family_fingerprint(family: dict[str, Any]) -> str:
    payload = json.dumps(_normalise_family_value(family), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _text_tokens(value: Any) -> set[str]:
    text = json.dumps(_normalise_family_value(value), ensure_ascii=False)
    tokens: set[str] = set()
    for raw_token in re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]+", text.lower()):
        if len(raw_token) > 1:
            tokens.add(raw_token)
        for part in raw_token.split("_"):
            if len(part) > 1:
                tokens.add(part)
    return tokens


def _family_tokens(family: dict[str, Any]) -> set[str]:
    return _text_tokens(family)


def _family_similarity(left: dict[str, Any], right: dict[str, Any]) -> float:
    if family_fingerprint(left) == family_fingerprint(right):
        return 1.0
    score = 0.0
    weights = {
        "core_signal": 0.30,
        "direction": 0.15,
        "selection": 0.10,
        "universe": 0.10,
        "rebalance_bars": 0.05,
    }
    for field, weight in weights.items():
        if field in left and field in right and _normalise_family_value(left[field]) == _normalise_family_value(right[field]):
            score += weight
    left_horizon = _literal_number(ast.Constant(left.get("holding_period_bars"))) if isinstance(left.get("holding_period_bars"), (int, float)) else None
    right_horizon = _literal_number(ast.Constant(right.get("holding_period_bars"))) if isinstance(right.get("holding_period_bars"), (int, float)) else None
    if left_horizon and right_horizon:
        ratio = max(left_horizon, right_horizon) / max(1.0, min(left_horizon, right_horizon))
        if ratio <= 1.25:
            score += 0.15
        elif ratio <= 2.0:
            score += 0.075
    left_tokens = _family_tokens(left)
    right_tokens = _family_tokens(right)
    union = left_tokens | right_tokens
    if union:
        score += 0.15 * (len(left_tokens & right_tokens) / len(union))
    return min(1.0, score)


def _normalised_identifier(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")


def _registry_alias_duplicates(candidate: dict[str, Any], registry_path: Path) -> list[str]:
    if not registry_path.is_file():
        return []
    registry = _json_mapping(registry_path)
    candidate_id = _normalised_identifier(candidate.get("candidate_id"))
    if not candidate_id:
        return []
    matches: list[str] = []
    rows = registry.get("families", [])
    if not isinstance(rows, list):
        return []
    for row in rows:
        if not isinstance(row, dict):
            continue
        identifiers = [row.get("family_id"), row.get("candidate_id")]
        aliases = row.get("aliases", [])
        if isinstance(aliases, list):
            identifiers.extend(aliases)
        normalised = {_normalised_identifier(item) for item in identifiers if item}
        if candidate_id in normalised:
            matches.append(str(row.get("family_id") or row.get("candidate_id") or candidate_id))
    return sorted(set(matches))


def _tag_matches(candidate_tokens: set[str], tag: str) -> bool:
    normalised = _normalised_identifier(tag)
    if not normalised:
        return False
    if normalised in candidate_tokens:
        return True
    parts = {part for part in normalised.split("_") if len(part) > 1}
    return bool(parts) and parts.issubset(candidate_tokens)


def _failure_fingerprint_matches(
    family: dict[str, Any],
    registry_path: Path,
) -> list[tuple[str, float, tuple[str, ...]]]:
    if not registry_path.is_file():
        return []
    registry = _json_mapping(registry_path)
    rows = registry.get("failure_fingerprints", [])
    if not isinstance(rows, list):
        return []
    candidate_tokens = _family_tokens(family)
    matches: list[tuple[str, float, tuple[str, ...]]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        tags = row.get("tags", [])
        if not isinstance(tags, list):
            continue
        distinctive_tags = [
            str(tag)
            for tag in tags
            if _normalised_identifier(tag)
            and _normalised_identifier(tag) not in FINGERPRINT_GENERIC_TAGS
        ]
        matched_tags = tuple(
            sorted(tag for tag in distinctive_tags if _tag_matches(candidate_tokens, tag))
        )
        coverage = len(matched_tags) / max(1, len(distinctive_tags))
        family_key = _normalised_identifier(row.get("family_key"))
        exact_key_match = bool(family_key and family_key in candidate_tokens)
        duplicate = exact_key_match or (
            len(matched_tags) >= MIN_FAILURE_FINGERPRINT_TAG_MATCHES
            and coverage >= MIN_FAILURE_FINGERPRINT_COVERAGE
        )
        if duplicate:
            matches.append(
                (
                    str(row.get("fingerprint_id") or row.get("family_key") or "failure_fingerprint"),
                    coverage,
                    matched_tags,
                )
            )
    return sorted(matches, key=lambda item: (item[1], len(item[2]), item[0]), reverse=True)


def _load_family_references(
    *,
    registry_path: Path,
    candidate_path: Path,
    archive_root: Path | None,
) -> list[tuple[str, dict[str, Any]]]:
    references: list[tuple[str, dict[str, Any]]] = []
    if registry_path.is_file():
        registry = _json_mapping(registry_path)
        rows = registry.get("families", [])
        if isinstance(rows, list):
            for row in rows:
                if not isinstance(row, dict):
                    continue
                family = row.get("family") if isinstance(row.get("family"), dict) else row.get("signature")
                if isinstance(family, dict):
                    references.append((str(row.get("family_id") or row.get("candidate_id") or "registry_family"), family))
    search_roots = [PROJECT_ROOT / "config" / "research_candidates"]
    if archive_root is not None and archive_root.is_dir():
        search_roots.append(archive_root)
    current_id = ""
    try:
        current_id = str(_json_mapping(candidate_path).get("candidate_id") or "")
    except Exception:
        pass
    for root in search_roots:
        if not root.is_dir():
            continue
        for path in root.rglob("*.json"):
            if path.resolve() == candidate_path.resolve() or path.name.startswith("PRE_PNL_CANDIDATE_TEMPLATE"):
                continue
            try:
                payload = _json_mapping(path)
            except Exception:
                continue
            candidate_id = str(payload.get("candidate_id") or path.stem)
            family = payload.get("family")
            if candidate_id == current_id or not isinstance(family, dict):
                continue
            references.append((candidate_id, family))
    unique: dict[str, tuple[str, dict[str, Any]]] = {}
    for reference_id, family in references:
        unique[f"{reference_id}:{family_fingerprint(family)}"] = (reference_id, family)
    return list(unique.values())


def run_family_duplicate_gate(
    candidate: dict[str, Any],
    candidate_path: Path,
    *,
    registry_path: Path = DEFAULT_FAMILY_REGISTRY,
    archive_root: Path | None = None,
) -> list[CheckResult]:
    family = candidate.get("family") if isinstance(candidate.get("family"), dict) else {}
    required = {"core_signal", "direction", "holding_period_bars", "selection", "universe"}
    missing = sorted(
        field
        for field in required
        if family.get(field) == [] or not _meaningful_text(family.get(field))
    )
    if missing:
        return [CheckResult("research", "family_signature_complete", False, ", ".join(missing))]
    references = _load_family_references(
        registry_path=registry_path,
        candidate_path=candidate_path,
        archive_root=archive_root,
    )
    similarities = sorted(
        ((reference_id, _family_similarity(family, reference_family)) for reference_id, reference_family in references),
        key=lambda item: item[1],
        reverse=True,
    )
    top_id, top_score = similarities[0] if similarities else ("none", 0.0)
    duplicates = [(reference_id, score) for reference_id, score in similarities if score >= MAX_FAMILY_SIMILARITY]
    alias_duplicates = _registry_alias_duplicates(candidate, registry_path)
    fingerprint_duplicates = _failure_fingerprint_matches(family, registry_path)
    fingerprint_detail = "none"
    if fingerprint_duplicates:
        fingerprint_id, coverage, matched_tags = fingerprint_duplicates[0]
        fingerprint_detail = (
            f"top={fingerprint_id} coverage={coverage:.4f} "
            f"matched={list(matched_tags)} matches={len(fingerprint_duplicates)}"
        )
    return [
        CheckResult("research", "family_signature_complete", True, family_fingerprint(family)),
        CheckResult(
            "research",
            "automatic_family_deduplication",
            not duplicates,
            f"top={top_id}:{top_score:.4f} threshold={MAX_FAMILY_SIMILARITY:.2f} references={len(references)}",
        ),
        CheckResult(
            "research",
            "registered_family_alias_deduplication",
            not alias_duplicates,
            ", ".join(alias_duplicates) if alias_duplicates else "none",
        ),
        CheckResult(
            "research",
            "failure_fingerprint_deduplication",
            not fingerprint_duplicates,
            fingerprint_detail,
        ),
    ]


def run_candidate_gate(candidate_path: Path, *, registry_path: Path = DEFAULT_FAMILY_REGISTRY, archive_root: Path | None = None) -> list[CheckResult]:
    try:
        candidate = _json_mapping(candidate_path)
    except Exception as exc:
        return [CheckResult("research", "candidate_json_valid", False, str(exc))]

    candidate_id = str(candidate.get("candidate_id", "")).strip()
    mechanism = candidate.get("mechanism") if isinstance(candidate.get("mechanism"), dict) else {}
    family = candidate.get("family") if isinstance(candidate.get("family"), dict) else {}
    data = candidate.get("data") if isinstance(candidate.get("data"), dict) else {}
    leakage = candidate.get("leakage") if isinstance(candidate.get("leakage"), dict) else {}
    historical_holdout = candidate.get("historical_holdout") if isinstance(candidate.get("historical_holdout"), dict) else {}
    trial_ledger = candidate.get("trial_ledger") if isinstance(candidate.get("trial_ledger"), dict) else {}
    point_in_time = candidate.get("point_in_time") if isinstance(candidate.get("point_in_time"), dict) else {}
    outcome_horizon = candidate.get("outcome_horizon") if isinstance(candidate.get("outcome_horizon"), dict) else {}
    dependency_manifest = candidate.get("code_dependency_manifest") if isinstance(candidate.get("code_dependency_manifest"), dict) else {}
    universe_selection = (
        candidate.get("universe_selection")
        if isinstance(candidate.get("universe_selection"), dict)
        else {}
    )
    candidate_symbols_raw = data.get("symbols")
    candidate_symbols = (
        [str(symbol).strip() for symbol in candidate_symbols_raw if str(symbol).strip()]
        if isinstance(candidate_symbols_raw, list)
        else []
    )
    try:
        universe_policy = _json_mapping(DEFAULT_UNIVERSE_POLICY)
    except Exception:
        universe_policy = {}
    allowed_symbols = {
        str(symbol).strip()
        for symbol in universe_policy.get("symbols", [])
        if str(symbol).strip()
    }
    subset_policy = (
        universe_policy.get("new_candidate_subset_policy")
        if isinstance(universe_policy.get("new_candidate_subset_policy"), dict)
        else {}
    )
    minimum_subset = int(subset_policy.get("minimum_symbols", 1))
    maximum_subset = int(subset_policy.get("maximum_symbols", len(allowed_symbols) or 21))
    invalid_symbols = sorted(set(candidate_symbols).difference(allowed_symbols))
    selection_basis = str(universe_selection.get("selection_basis", "")).strip()
    parameter_audit = audit_parameter_space(candidate)
    robustness_protocol_ok, robustness_protocol_detail = frozen_protocol_ok(
        candidate.get("robustness_protocol")
    )
    data_fields_raw = data.get("fields")
    data_fields = [str(item).strip() for item in data_fields_raw if str(item).strip()] if isinstance(data_fields_raw, list) else []
    code_files = _candidate_code_files(candidate, candidate_path)

    holdout_start = _parse_utc_timestamp(historical_holdout.get("start_utc"))
    holdout_end = _parse_utc_timestamp(historical_holdout.get("end_utc"))
    data_start = _parse_utc_timestamp(data.get("start_utc"))
    data_end = _parse_utc_timestamp(data.get("end_utc"))
    try:
        holdout_months = int(historical_holdout.get("months"))
    except (TypeError, ValueError):
        holdout_months = 0
    holdout_days = (holdout_end - holdout_start).total_seconds() / 86400.0 if holdout_start and holdout_end else 0.0
    expected_holdout_days = holdout_months * 30.4375
    holdout_window_ok = bool(
        MIN_HOLDOUT_MONTHS <= holdout_months <= MAX_HOLDOUT_MONTHS
        and MIN_HOLDOUT_DAYS <= holdout_days <= MAX_HOLDOUT_DAYS
        and abs(holdout_days - expected_holdout_days) <= 20.0
        and holdout_start
        and holdout_end
        and data_start
        and data_end
        and data_start < holdout_start < holdout_end <= data_end
    )
    holdout_hash_ok, holdout_hash_detail, holdout_manifest_path = _hashed_evidence_status(
        historical_holdout.get("split_manifest_file"),
        historical_holdout.get("split_manifest_sha256"),
        candidate_path,
    )
    holdout_manifest_ok, holdout_manifest_detail = _holdout_manifest_status(
        holdout_manifest_path,
        candidate_id=candidate_id,
        months=historical_holdout.get("months"),
        start_utc=historical_holdout.get("start_utc"),
        end_utc=historical_holdout.get("end_utc"),
        data_snapshot_sha256=historical_holdout.get("data_snapshot_sha256"),
        split_sha256=historical_holdout.get("split_sha256"),
    )

    trial_hash_ok, trial_hash_detail, trial_path = _hashed_evidence_status(
        trial_ledger.get("file"), trial_ledger.get("sha256"), candidate_path
    )
    trial_content_ok, trial_content_detail = _trial_ledger_status(
        trial_path, candidate_id, trial_ledger.get("family_trial_count")
    )

    point_hash_ok, point_hash_detail, point_path = _hashed_evidence_status(
        point_in_time.get("evidence_file"), point_in_time.get("evidence_sha256"), candidate_path
    )
    point_content_ok, point_content_detail = _point_in_time_evidence_status(point_path, data_fields)

    dependency_hash_ok, dependency_hash_detail, dependency_path = _hashed_evidence_status(
        dependency_manifest.get("file"), dependency_manifest.get("sha256"), candidate_path
    )
    dependency_content_ok, dependency_content_detail = _dependency_manifest_status(dependency_path, code_files)

    def _positive_int(value: Any) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return 0
        return parsed if parsed > 0 else 0

    max_holding_bars = _positive_int(outcome_horizon.get("max_holding_bars"))
    label_horizon_bars = _positive_int(outcome_horizon.get("label_horizon_bars"))
    purge_bars = _positive_int(outcome_horizon.get("purge_bars"))
    embargo_bars = _positive_int(outcome_horizon.get("embargo_bars"))
    family_holding_bars = _positive_int(family.get("holding_period_bars"))
    outcome_horizon_ok = bool(
        outcome_horizon.get("locked_before_pnl") is True
        and max_holding_bars >= family_holding_bars > 0
        and label_horizon_bars >= max_holding_bars
        and purge_bars >= label_horizon_bars
        and embargo_bars > 0
    )

    results = [
        CheckResult("research", "candidate_schema_v2", candidate.get("schema") == "okx_pre_pnl_candidate_v2", str(candidate.get("schema"))),
        CheckResult("research", "candidate_id_present", bool(str(candidate.get("candidate_id", "")).strip()), str(candidate.get("candidate_id", ""))),
        CheckResult("research", "payer_identified", bool(str(mechanism.get("payer", "")).strip()), str(mechanism.get("payer", ""))),
        CheckResult("research", "unique_direction_identified", bool(str(mechanism.get("direction", "")).strip()), str(mechanism.get("direction", ""))),
        CheckResult("research", "observable_proxy_identified", bool(str(mechanism.get("observable_proxy", "")).strip()), str(mechanism.get("observable_proxy", ""))),
        CheckResult("research", "same_exchange_okx_only", data.get("exchange") == "OKX" and data.get("cross_exchange") is False and data.get("local_only") is True, json.dumps(data, ensure_ascii=False)),
        CheckResult("research", "closed_data_only", data.get("closed_only") is True, str(data.get("closed_only"))),
        CheckResult(
            "research",
            "candidate_symbol_subset_explicit",
            bool(candidate_symbols),
            json.dumps(candidate_symbols, ensure_ascii=False),
        ),
        CheckResult(
            "research",
            "candidate_symbol_subset_unique",
            bool(candidate_symbols) and len(candidate_symbols) == len(set(candidate_symbols)),
            f"count={len(candidate_symbols)} unique={len(set(candidate_symbols))}",
        ),
        CheckResult(
            "research",
            "candidate_symbol_subset_within_allowed_pool",
            bool(candidate_symbols) and bool(allowed_symbols) and not invalid_symbols,
            ", ".join(invalid_symbols) or f"allowed_pool={len(allowed_symbols)}",
        ),
        CheckResult(
            "research",
            "candidate_symbol_subset_size_allowed",
            minimum_subset <= len(candidate_symbols) <= maximum_subset,
            f"count={len(candidate_symbols)} allowed={minimum_subset}-{maximum_subset}",
        ),
        CheckResult(
            "research",
            "candidate_symbol_selection_locked_before_pnl",
            universe_selection.get("selection_locked_before_pnl") is True
            and universe_selection.get("outcome_based_selection") is False
            and universe_selection.get("legacy_outcomes_used_to_choose_subset") is False,
            json.dumps(universe_selection, ensure_ascii=False),
        ),
        CheckResult(
            "research",
            "candidate_symbol_selection_basis_present",
            bool(selection_basis),
            selection_basis,
        ),
        CheckResult(
            "research",
            "future_returns_closed",
            leakage.get("future_returns_opened") is False
            and leakage.get("pnl_opened") is False
            and leakage.get("entry_uses_next_tradable_price") is True,
            json.dumps(leakage, ensure_ascii=False),
        ),
        CheckResult(
            "research",
            "historical_holdout_precommitted_unopened",
            historical_holdout.get("locked_before_pnl") is True
            and historical_holdout.get("rules_frozen_before_holdout") is True
            and historical_holdout.get("opened_count") == 0
            and historical_holdout.get("opened_at_utc") is None,
            json.dumps(historical_holdout, ensure_ascii=False),
        ),
        CheckResult(
            "research",
            "historical_holdout_window_6_to_10_months",
            holdout_window_ok,
            f"months={holdout_months} days={holdout_days:.2f} expected_days={expected_holdout_days:.2f} data_start={data_start} holdout_start={holdout_start} holdout_end={holdout_end} data_end={data_end}",
        ),
        CheckResult(
            "research",
            "historical_holdout_commitment_hashes_present",
            _valid_sha256(historical_holdout.get("data_snapshot_sha256"))
            and _valid_sha256(historical_holdout.get("split_sha256")),
            f"data_snapshot_sha256={historical_holdout.get('data_snapshot_sha256')} split_sha256={historical_holdout.get('split_sha256')}",
        ),
        CheckResult(
            "research",
            "historical_holdout_manifest_hash_verified",
            holdout_hash_ok,
            holdout_hash_detail,
        ),
        CheckResult(
            "research",
            "historical_holdout_manifest_content_verified",
            holdout_manifest_ok,
            holdout_manifest_detail,
        ),
        CheckResult(
            "research",
            "complete_family_trial_ledger_declared",
            trial_ledger.get("all_family_trials_recorded") is True
            and trial_ledger.get("registered_before_pnl") is True
            and _positive_int(trial_ledger.get("family_trial_count")) > 0,
            json.dumps(trial_ledger, ensure_ascii=False),
        ),
        CheckResult("research", "family_trial_ledger_hash_verified", trial_hash_ok, trial_hash_detail),
        CheckResult("research", "family_trial_ledger_content_verified", trial_content_ok, trial_content_detail),
        CheckResult(
            "research",
            "point_in_time_policy_declared",
            point_in_time.get("all_fields_have_available_at") is True
            and point_in_time.get("revisions_frozen_or_versioned") is True
            and point_in_time.get("no_current_metadata_backfill") is True
            and point_in_time.get("signal_after_data_available") is True
            and point_in_time.get("execution_after_signal") is True,
            json.dumps(point_in_time, ensure_ascii=False),
        ),
        CheckResult("research", "point_in_time_evidence_hash_verified", point_hash_ok, point_hash_detail),
        CheckResult("research", "point_in_time_evidence_content_verified", point_content_ok, point_content_detail),
        CheckResult(
            "research",
            "purge_covers_complete_outcome_horizon",
            outcome_horizon_ok,
            f"family_holding={family_holding_bars} max_holding={max_holding_bars} label_horizon={label_horizon_bars} purge={purge_bars} embargo={embargo_bars}",
        ),
        CheckResult(
            "research",
            "code_dependency_manifest_declared_complete",
            dependency_manifest.get("complete") is True,
            json.dumps(dependency_manifest, ensure_ascii=False),
        ),
        CheckResult("research", "code_dependency_manifest_hash_verified", dependency_hash_ok, dependency_hash_detail),
        CheckResult("research", "code_dependency_manifest_content_verified", dependency_content_ok, dependency_content_detail),
        CheckResult(
            "research",
            "automatic_parameter_freedom",
            parameter_audit.bounded,
            f"free={parameter_audit.free_parameters}/{MAX_FREE_PARAMETERS} combinations={parameter_audit.combinations}/{MAX_PARAMETER_COMBINATIONS} problems={list(parameter_audit.problems)}",
        ),
        CheckResult(
            "research",
            "robustness_protocol_frozen",
            robustness_protocol_ok,
            robustness_protocol_detail,
        ),
        CheckResult("research", "representation_invariance_passed", candidate.get("representation_invariance_passed") is True, str(candidate.get("representation_invariance_passed"))),
        CheckResult("research", "measurement_semantics_passed", candidate.get("measurement_semantics_passed") is True, str(candidate.get("measurement_semantics_passed"))),
    ]
    results.extend(
        run_family_duplicate_gate(
            candidate,
            candidate_path,
            registry_path=registry_path,
            archive_root=archive_root,
        )
    )

    missing = [str(path) for path in code_files if not path.is_file()]
    results.append(
        CheckResult(
            "research",
            "candidate_code_files_exist",
            bool(code_files) and not missing,
            ", ".join(missing) if missing else f"count={len(code_files)}",
        )
    )
    hits: list[str] = []
    parse_failures: list[str] = []
    for path in code_files:
        if not path.is_file():
            continue
        parsed, path_hits = scan_future_leaks(path)
        if not parsed:
            parse_failures.extend(f"{path.name}:{hit}" for hit in path_hits)
        hits.extend(f"{path.name}:{hit}" for hit in path_hits if not hit.startswith("parse_error:"))
    results.append(CheckResult("research", "future_leak_code_parse", not parse_failures, ", ".join(parse_failures) or "all parsed"))
    results.append(CheckResult("research", "automatic_future_leak_scan", bool(code_files) and not hits, ", ".join(hits) or "clean"))
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


def contribution_metrics(trades: pd.DataFrame) -> dict[str, float | int]:
    frame = trades.copy()
    if "exit_time" in frame:
        parsed = pd.to_datetime(frame["exit_time"], utc=True, errors="coerce")
    elif "entry_time" in frame:
        parsed = pd.to_datetime(frame["entry_time"], utc=True, errors="coerce")
    else:
        parsed = pd.Series(pd.NaT, index=frame.index, dtype="datetime64[ns, UTC]")
    frame["_month"] = parsed.dt.strftime("%Y-%m")
    net_r = pd.to_numeric(frame.get("net_r", pd.Series(dtype=float)), errors="coerce").fillna(0.0)
    positive = net_r[net_r > 0.0].sort_values(ascending=False)
    positive_total = float(positive.sum())
    if positive_total <= 0.0:
        top_one = top_three = 1.0
        effective = 0.0
    else:
        shares = positive / positive_total
        top_one = float(shares.iloc[0])
        top_three = float(shares.head(3).sum())
        effective = float(1.0 / (shares.pow(2).sum())) if float(shares.pow(2).sum()) > 0.0 else 0.0
    return {
        "total_trades": int(len(frame)),
        "positive_trades": int(len(positive)),
        "single_symbol_share": _positive_contribution_share(frame, "inst_id"),
        "single_month_share": _positive_contribution_share(frame, "_month"),
        "single_trade_share": top_one,
        "top_three_trade_share": top_three,
        "effective_positive_trades": effective,
    }


def evaluate_symbol_contribution_gate(
    *,
    declared_symbol_count: int,
    single_symbol_share: float,
) -> tuple[bool, str]:
    if 0 < declared_symbol_count < MIN_SYMBOLS_FOR_CROSS_SYMBOL_CONTRIBUTION_GATE:
        return (
            True,
            f"not_applicable=frozen_subset_size_{declared_symbol_count}; "
            "time/month/trade/regime robustness remains mandatory",
        )
    return (
        single_symbol_share <= MAX_SYMBOL_CONTRIBUTION,
        f"share={single_symbol_share:.4f} max={MAX_SYMBOL_CONTRIBUTION:.2f}",
    )


def _stress_metric(stress: pd.DataFrame, scenario: str, column: str, default: float) -> float:
    if stress.empty or "scenario" not in stress or column not in stress:
        return default
    rows = stress.loc[stress["scenario"].astype(str) == scenario, column]
    if rows.empty:
        return default
    try:
        value = float(rows.iloc[0])
    except (TypeError, ValueError):
        return default
    return value if math.isfinite(value) else default


def execute_cost_stress(artifact_dir: Path) -> tuple[list[CheckResult], pd.DataFrame]:
    trades_path = artifact_dir / "sample_trades.csv"
    if not trades_path.is_file():
        return [CheckResult("research", "cost_stress_input_present", False, str(trades_path))], pd.DataFrame()
    try:
        trades = pd.read_csv(trades_path)
        required_trade_facts = {
            "inst_id",
            "entry_time",
            "exit_time",
            "side",
            "entry_price",
            "exit_price",
            "qty",
            "gross_pnl",
            "risk_amount",
            "leverage_used",
        }
        missing_trade_facts = sorted(required_trade_facts - set(trades.columns))
        if missing_trade_facts:
            return [
                CheckResult(
                    "research",
                    "cost_stress_trade_facts_complete",
                    False,
                    ", ".join(missing_trade_facts),
                )
            ], pd.DataFrame()
        from okx_signal_system.backtest.research import replay_cost_stress

        stress = replay_cost_stress(trades)
        temporary = artifact_dir / ".cost_stress.csv.tmp"
        stress.to_csv(temporary, index=False)
        temporary.replace(artifact_dir / "cost_stress.csv")
    except Exception as exc:
        return [CheckResult("research", "cost_stress_execution", False, str(exc))], pd.DataFrame()
    scenarios = set(stress.get("scenario", pd.Series(dtype=str)).astype(str))
    sources = set(stress.get("recompute_source", pd.Series(dtype=str)).astype(str))
    complete = {"baseline", "stress_1_5x", "stress_2x"}.issubset(scenarios)
    provenance_ok = bool(sources) and "legacy_cost_fallback" not in sources
    baseline_ok = bool(
        _stress_metric(stress, "baseline", "profit_factor", 0.0) >= 1.05
        and _stress_metric(stress, "baseline", "net_r", -1.0) > 0.0
        and _stress_metric(stress, "baseline", "max_drawdown", 1.0) <= 0.25
        and _stress_metric(stress, "baseline", "total_trades", 0.0) >= MIN_VALIDATION_TRADES
    )
    stress_15_ok = bool(
        _stress_metric(stress, "stress_1_5x", "profit_factor", 0.0) >= 1.0
        and _stress_metric(stress, "stress_1_5x", "net_r", -1.0) >= 0.0
        and _stress_metric(stress, "stress_1_5x", "max_drawdown", 1.0) <= 0.30
    )
    stress_20_ok = bool(
        _stress_metric(stress, "stress_2x", "net_r", -999.0) >= -2.0
        and _stress_metric(stress, "stress_2x", "max_drawdown", 1.0) <= 0.35
    )
    return [
        CheckResult("research", "cost_stress_execution", not stress.empty, f"rows={len(stress)}"),
        CheckResult("research", "cost_stress_scenarios_complete", complete, ", ".join(sorted(scenarios))),
        CheckResult("research", "cost_stress_recomputed_from_trade_facts", provenance_ok, ", ".join(sorted(sources)) or "missing"),
        CheckResult(
            "research",
            "cost_stress_metrics_passed",
            baseline_ok and stress_15_ok and stress_20_ok,
            json.dumps(
                {
                    "baseline": {
                        "pf": _stress_metric(stress, "baseline", "profit_factor", 0.0),
                        "net_r": _stress_metric(stress, "baseline", "net_r", 0.0),
                        "dd": _stress_metric(stress, "baseline", "max_drawdown", 1.0),
                    },
                    "stress_1_5x": {
                        "pf": _stress_metric(stress, "stress_1_5x", "profit_factor", 0.0),
                        "net_r": _stress_metric(stress, "stress_1_5x", "net_r", 0.0),
                        "dd": _stress_metric(stress, "stress_1_5x", "max_drawdown", 1.0),
                    },
                    "stress_2x": {
                        "net_r": _stress_metric(stress, "stress_2x", "net_r", 0.0),
                        "dd": _stress_metric(stress, "stress_2x", "max_drawdown", 1.0),
                    },
                },
                ensure_ascii=False,
            ),
        ),
    ], stress


def run_artifact_gate(
    artifact_dir: Path,
    *,
    candidate: dict[str, Any] | None = None,
) -> list[CheckResult]:
    results: list[CheckResult] = []
    required = {
        "acceptance_checklist.csv",
        "sample_trades.csv",
        "portfolio_results.csv",
        "candidate_params.json",
    }
    missing = sorted(name for name in required if not (artifact_dir / name).is_file())
    results.append(CheckResult("research", "required_artifacts_present", not missing, ", ".join(missing) or "complete"))
    if missing:
        return results

    cost_results, stress = execute_cost_stress(artifact_dir)
    results.extend(cost_results)

    checklist = pd.read_csv(artifact_dir / "acceptance_checklist.csv")
    checks_present = set(checklist.get("check", pd.Series(dtype=str)).astype(str))
    passed = _bool_series(checklist.get("passed", pd.Series(dtype=bool)))
    failed_names = checklist.loc[~passed, "check"].astype(str).tolist() if "check" in checklist else ["invalid_checklist"]
    results.append(CheckResult("research", "acceptance_checklist_all_passed", bool(len(checklist)) and bool(passed.all()), ", ".join(failed_names) or "all passed"))
    missing_checks = sorted(REQUIRED_RESEARCH_CHECKS - checks_present)
    results.append(CheckResult("research", "required_acceptance_checks_present", not missing_checks, ", ".join(missing_checks) or "complete"))

    trades = pd.read_csv(artifact_dir / "sample_trades.csv")
    metrics = contribution_metrics(trades)
    candidate_data = (
        candidate.get("data")
        if isinstance(candidate, dict) and isinstance(candidate.get("data"), dict)
        else {}
    )
    declared_symbols_raw = candidate_data.get("symbols")
    declared_symbols = (
        [str(symbol).strip() for symbol in declared_symbols_raw if str(symbol).strip()]
        if isinstance(declared_symbols_raw, list)
        else []
    )
    traded_symbols = sorted(
        {
            str(symbol).strip()
            for symbol in trades.get("inst_id", pd.Series(dtype=str)).dropna().astype(str)
            if str(symbol).strip()
        }
    )
    undeclared_traded_symbols = sorted(set(traded_symbols).difference(declared_symbols))
    if candidate is not None:
        results.append(
            CheckResult(
                "research",
                "sample_trades_within_frozen_subset",
                bool(declared_symbols) and not undeclared_traded_symbols,
                ", ".join(undeclared_traded_symbols)
                or f"declared={len(declared_symbols)} traded={len(traded_symbols)}",
            )
        )
    symbol_contribution_ok, symbol_contribution_detail = evaluate_symbol_contribution_gate(
        declared_symbol_count=len(declared_symbols),
        single_symbol_share=float(metrics["single_symbol_share"]),
    )
    results.extend(
        [
            CheckResult(
                "research",
                "validation_trade_count_sufficient",
                int(metrics["total_trades"]) >= MIN_VALIDATION_TRADES,
                f"trades={metrics['total_trades']} minimum={MIN_VALIDATION_TRADES}",
            ),
            CheckResult(
                "research",
                "single_symbol_positive_contribution_bounded",
                symbol_contribution_ok,
                symbol_contribution_detail,
            ),
            CheckResult(
                "research",
                "single_month_positive_contribution_bounded",
                float(metrics["single_month_share"]) <= MAX_MONTH_CONTRIBUTION,
                f"share={float(metrics['single_month_share']):.4f} max={MAX_MONTH_CONTRIBUTION:.2f}",
            ),
            CheckResult(
                "research",
                "single_trade_positive_contribution_bounded",
                float(metrics["single_trade_share"]) <= MAX_SINGLE_TRADE_CONTRIBUTION,
                f"share={float(metrics['single_trade_share']):.4f} max={MAX_SINGLE_TRADE_CONTRIBUTION:.2f}",
            ),
            CheckResult(
                "research",
                "top_three_trade_positive_contribution_bounded",
                float(metrics["top_three_trade_share"]) <= MAX_TOP_THREE_TRADE_CONTRIBUTION,
                f"share={float(metrics['top_three_trade_share']):.4f} max={MAX_TOP_THREE_TRADE_CONTRIBUTION:.2f}",
            ),
            CheckResult(
                "research",
                "effective_positive_trade_count_sufficient",
                float(metrics["effective_positive_trades"]) >= MIN_EFFECTIVE_POSITIVE_TRADES,
                f"effective={float(metrics['effective_positive_trades']):.2f} minimum={MIN_EFFECTIVE_POSITIVE_TRADES:.0f}",
            ),
        ]
    )

    candidate_payload = _json_mapping(artifact_dir / "candidate_params.json")
    results.append(CheckResult("research", "formal_candidate_type", candidate_payload.get("artifact_type") == "strict_research_candidate", str(candidate_payload.get("artifact_type"))))
    results.append(CheckResult("research", "promotion_requires_manual_gate", candidate_payload.get("promotion_eligible") is True, str(candidate_payload.get("promotion_eligible"))))

    robustness_report = evaluate_robustness_screen(
        artifact_dir,
        protocol=(candidate or {}).get("robustness_protocol"),
    )
    _write_json_atomic(artifact_dir / "robustness_screen.json", robustness_report)
    for item in robustness_report.get("checks", []):
        if not isinstance(item, dict):
            continue
        results.append(
            CheckResult(
                "research",
                str(item.get("name") or "robustness_screen_check"),
                item.get("ok") is True,
                str(item.get("detail") or ""),
            )
        )
    return results


def _timeframe_seconds(timeframe: str) -> int:
    match = re.fullmatch(r"(\d+)([mhd])", str(timeframe).strip().lower())
    if not match:
        raise ValueError(f"unsupported timeframe: {timeframe}")
    value = int(match.group(1))
    multiplier = {"m": 60, "h": 3600, "d": 86400}[match.group(2)]
    return value * multiplier


def _runtime_filename(symbol: str, timeframe: str) -> str:
    normalized = symbol.replace("-", "_").replace("_SWAP", "").upper()
    if normalized.count("USDT") == 1:
        normalized = f"{normalized}_USDT"
    return f"{normalized}_{timeframe}.parquet"


def _closed_mask(series: pd.Series) -> pd.Series:
    if pd.api.types.is_bool_dtype(series):
        return series.fillna(False)
    return series.astype("string").str.strip().str.lower().isin({"true", "1", "yes"})


def _summarize_research_data_files(
    paths: list[Path],
    *,
    symbol: str,
    timeframe: str,
    baseline: datetime | None,
) -> dict[str, Any]:
    timestamp_parts: list[pd.Series] = []
    errors: list[str] = []
    used_paths: list[str] = []
    source_duplicate_count = 0
    for path in paths:
        try:
            frame = pd.read_parquet(path, columns=["ts", "is_closed"])
        except Exception as exc:
            errors.append(f"{path}:{exc}")
            continue
        if "ts" not in frame or "is_closed" not in frame:
            errors.append(f"{path}:missing ts/is_closed")
            continue
        timestamps = pd.to_datetime(
            frame.loc[_closed_mask(frame["is_closed"]), "ts"],
            utc=True,
            errors="coerce",
        ).dropna()
        if timestamps.empty:
            errors.append(f"{path}:no closed bars")
            continue
        source_duplicate_count += int(len(timestamps) - len(timestamps.drop_duplicates()))
        timestamp_parts.append(timestamps)
        used_paths.append(str(path))
    if not timestamp_parts:
        return {
            "symbol": symbol,
            "paths": [str(path) for path in paths],
            "ok": False,
            "error": "; ".join(errors) or "no readable closed data",
        }
    timestamps = pd.concat(timestamp_parts, ignore_index=True).sort_values()
    unique = timestamps.drop_duplicates()
    overlap_count = max(0, int(len(timestamps) - len(unique) - source_duplicate_count))
    first = unique.iloc[0]
    latest = unique.iloc[-1]
    seconds = _timeframe_seconds(timeframe)
    expected = int((latest - first).total_seconds() // seconds) + 1
    missing = max(0, expected - len(unique))
    gap_ratio = float(missing / expected) if expected else 1.0
    baseline_ts = pd.Timestamp(baseline) if baseline is not None else None
    new_bars = int((unique > baseline_ts).sum()) if baseline_ts is not None else int(len(unique))
    return {
        "symbol": symbol,
        "paths": used_paths,
        "ok": True,
        "closed_bars": int(len(unique)),
        "duplicate_bars": source_duplicate_count,
        "source_overlap_bars": overlap_count,
        "first_closed": first.isoformat(),
        "latest_closed": latest.isoformat(),
        "history_days": float((latest - first).total_seconds() / 86400.0),
        "gap_ratio": gap_ratio,
        "new_bars": new_bars,
        "source_errors": errors,
    }


def _research_data_roots(dataset: str, data_root: Path | None) -> list[Path]:
    if data_root is not None:
        return [data_root]
    roots: list[Path] = []
    try:
        from okx_signal_system.paths import find_lightweight_history

        roots.append(find_lightweight_history(dataset))
    except Exception:
        pass
    retained_history = PROJECT_ROOT.parents[1] / "历史数据_保留" / "lightweight_history" / dataset
    if retained_history.is_dir():
        roots.append(retained_history)
    runtime_root = PROJECT_ROOT / "outputs" / "runtime_cache" / "lightweight_history" / dataset
    if runtime_root.is_dir():
        roots.append(runtime_root)
    unique: list[Path] = []
    for root in roots:
        resolved = root.resolve()
        if resolved not in unique:
            unique.append(resolved)
    return unique


def evaluate_data_readiness(
    *,
    dataset: str,
    timeframe: str,
    state_file: Path = DEFAULT_DATA_STATE,
    data_root: Path | None = None,
    symbols: list[str] | tuple[str, ...] | None = None,
    min_symbols: int = DEFAULT_MIN_RESEARCH_SYMBOLS,
    min_history_days: float = DEFAULT_MIN_HISTORY_DAYS,
    min_new_days: float = DEFAULT_MIN_NEW_DATA_DAYS,
    max_gap_ratio: float = DEFAULT_MAX_GAP_RATIO,
    coverage_ratio: float = DEFAULT_DATA_COVERAGE_RATIO,
) -> DataReadiness:
    previous: dict[str, Any] = {}
    if state_file.is_file():
        try:
            previous = _json_mapping(state_file)
        except Exception:
            previous = {}
    previous_latest = previous.get("latest_closed_by_symbol") if isinstance(previous.get("latest_closed_by_symbol"), dict) else {}
    initial_research = not bool(previous_latest)
    required_new_bars = max(1, int(math.ceil(min_new_days * 86400.0 / _timeframe_seconds(timeframe))))
    roots = _research_data_roots(dataset, data_root)
    target_symbols = list(dict.fromkeys(symbols or configured_symbols()))
    rows: list[dict[str, Any]] = []
    latest_closed_by_symbol: dict[str, str] = {}
    for symbol in target_symbols:
        baseline = _parse_time(previous_latest.get(symbol))
        candidates = [root / _runtime_filename(symbol, timeframe) for root in roots]
        available = [path for path in candidates if path.is_file()]
        if available:
            best = _summarize_research_data_files(
                available,
                symbol=symbol,
                timeframe=timeframe,
                baseline=baseline,
            )
        else:
            best = {"symbol": symbol, "ok": False, "error": "data file missing", "paths_checked": [str(path) for path in candidates]}
        history_ok = bool(
            best.get("ok")
            and float(best.get("history_days", 0.0)) >= min_history_days
            and int(best.get("duplicate_bars", 1)) == 0
            and float(best.get("gap_ratio", 1.0)) <= max_gap_ratio
        )
        new_data_ok = history_ok and (initial_research or int(best.get("new_bars", 0)) >= required_new_bars)
        best["history_ok"] = history_ok
        best["new_data_ok"] = new_data_ok
        rows.append(best)
        if best.get("ok") and best.get("latest_closed"):
            latest_closed_by_symbol[symbol] = str(best["latest_closed"])
    symbol_count = len(target_symbols)
    required_coverage = max(min_symbols, int(math.ceil(symbol_count * coverage_ratio)))
    covered = sum(bool(row.get("ok")) for row in rows)
    history_qualified = sum(bool(row.get("history_ok")) for row in rows)
    new_qualified = sum(bool(row.get("new_data_ok")) for row in rows)
    ready = bool(
        symbol_count >= min_symbols
        and covered >= required_coverage
        and history_qualified >= required_coverage
        and new_qualified >= required_coverage
    )
    return DataReadiness(
        dataset=dataset,
        timeframe=timeframe,
        ready=ready,
        initial_research=initial_research,
        symbol_count=symbol_count,
        covered_symbols=covered,
        history_qualified_symbols=history_qualified,
        new_data_qualified_symbols=new_qualified,
        required_new_bars=required_new_bars,
        latest_closed_by_symbol=latest_closed_by_symbol,
        rows=tuple(rows),
    )


def run_data_readiness(
    *,
    dataset: str,
    timeframe: str,
    state_file: Path = DEFAULT_DATA_STATE,
    report_file: Path | None = DEFAULT_DATA_REPORT,
    data_root: Path | None = None,
    symbols: list[str] | tuple[str, ...] | None = None,
    min_symbols: int = DEFAULT_MIN_RESEARCH_SYMBOLS,
    min_history_days: float = DEFAULT_MIN_HISTORY_DAYS,
    min_new_days: float = DEFAULT_MIN_NEW_DATA_DAYS,
    max_gap_ratio: float = DEFAULT_MAX_GAP_RATIO,
    coverage_ratio: float = DEFAULT_DATA_COVERAGE_RATIO,
) -> tuple[list[CheckResult], DataReadiness]:
    readiness = evaluate_data_readiness(
        dataset=dataset,
        timeframe=timeframe,
        state_file=state_file,
        data_root=data_root,
        symbols=symbols,
        min_symbols=min_symbols,
        min_history_days=min_history_days,
        min_new_days=min_new_days,
        max_gap_ratio=max_gap_ratio,
        coverage_ratio=coverage_ratio,
    )
    required_coverage = max(min_symbols, int(math.ceil(readiness.symbol_count * coverage_ratio)))
    integrity_failures = [
        str(row.get("symbol"))
        for row in readiness.rows
        if row.get("ok") and (int(row.get("duplicate_bars", 0)) > 0 or float(row.get("gap_ratio", 0.0)) > max_gap_ratio)
    ]
    results = [
        CheckResult(
            "data",
            "research_symbol_coverage",
            readiness.covered_symbols >= required_coverage,
            f"covered={readiness.covered_symbols}/{readiness.symbol_count} required={required_coverage}",
        ),
        CheckResult(
            "data",
            "research_history_depth",
            readiness.history_qualified_symbols >= required_coverage,
            f"qualified={readiness.history_qualified_symbols}/{readiness.symbol_count} min_days={min_history_days:g}",
        ),
        CheckResult(
            "data",
            "research_data_integrity",
            not integrity_failures and readiness.covered_symbols >= required_coverage,
            ", ".join(integrity_failures) or f"max_gap_ratio={max_gap_ratio:.2%}",
        ),
        CheckResult(
            "data",
            "new_data_research_threshold",
            readiness.new_data_qualified_symbols >= required_coverage,
            f"initial={readiness.initial_research} qualified={readiness.new_data_qualified_symbols}/{readiness.symbol_count} required_new_bars={readiness.required_new_bars}",
        ),
    ]
    if report_file is not None:
        _write_json_atomic(
            report_file,
            {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "version": __version__,
                "readiness": asdict(readiness),
                "checks": [asdict(item) for item in results],
            },
        )
    return results, readiness


def mark_research_data_state(readiness: DataReadiness, state_file: Path = DEFAULT_DATA_STATE) -> None:
    _write_json_atomic(
        state_file,
        {
            "schema": "okx_research_data_state_v1",
            "marked_at": datetime.now(timezone.utc).isoformat(),
            "version": __version__,
            "dataset": readiness.dataset,
            "timeframe": readiness.timeframe,
            "latest_closed_by_symbol": readiness.latest_closed_by_symbol,
        },
    )


def _default_failure_archive_root() -> Path:
    configured = os.environ.get("FAILED_RESEARCH_ARCHIVE_DIR", "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path.home() / "Desktop" / "失败策略"


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
    failed_checks = [asdict(item) for item in results if item.category == "research" and item.blocking and not item.ok]
    failure_material = candidate_path.read_bytes() + json.dumps(failed_checks, sort_keys=True, ensure_ascii=False).encode("utf-8")
    failure_hash = hashlib.sha256(failure_material).hexdigest()
    archive_root.mkdir(parents=True, exist_ok=True)
    destination = archive_root / f"{safe_id}_{failure_hash[:12]}"
    if destination.is_dir() and (destination / "failure_summary.json").is_file():
        return destination
    destination.mkdir(parents=True, exist_ok=False)
    shutil.copy2(candidate_path, destination / candidate_path.name)
    if artifact_dir and artifact_dir.is_dir():
        for name in (
            "acceptance_checklist.csv",
            "cost_stress.csv",
            "portfolio_results.csv",
            "sample_trades.csv",
            "candidate_params.json",
            "final_report.md",
            "research_gate_report.json",
            "robustness_screen.json",
        ):
            source = artifact_dir / name
            if source.is_file():
                shutil.copy2(source, destination / name)
    summary = {
        "status": "REJECT_AND_ARCHIVE_NO_RESCUE",
        "candidate_id": candidate_id,
        "archived_at": datetime.now(timezone.utc).isoformat(),
        "failure_hash": failure_hash,
        "failed_checks": failed_checks,
    }
    _write_json_atomic(destination / "failure_summary.json", summary)
    markdown_lines = [
        f"# {candidate_id} 失败说明",
        "",
        "- 状态：`REJECT_AND_ARCHIVE_NO_RESCUE`",
        f"- 归档时间：`{summary['archived_at']}`",
        f"- 失败哈希：`{failure_hash}`",
        "- 生产系统影响：`NONE`",
        "",
        "## 失败门禁",
        "",
    ]
    for item in failed_checks:
        markdown_lines.append(f"- `{item.get('name', 'unknown')}`：{item.get('detail', '')}")
    if not failed_checks:
        markdown_lines.append("- 未提供结构化失败项，请查看原始报告。")
    markdown_lines.extend(
        [
            "",
            "## 固定结论",
            "",
            "该候选已永久归档，不得通过事后调参、删币、改方向、降低成本或更名重新进入候选池。",
            "",
        ]
    )
    (destination / "失败说明.md").write_text("\n".join(markdown_lines), encoding="utf-8")
    return destination


async def run_shadow_check(*, write_runtime_output: bool = False) -> list[CheckResult]:
    from okx_signal_system.shadow_ensemble import ShadowEnsembleService, ShadowEnsembleStore, load_shadow_ensemble_config

    base = load_config("base.yaml")
    symbols = [str(item) for item in base.get("data", {}).get("symbols", [])]
    cache_dir = PROJECT_ROOT / "outputs" / "runtime_cache" / "lightweight_history" / "okx_15m_extended"
    if not cache_dir.is_dir():
        return [CheckResult("shadow", "runtime_cache_exists", False, str(cache_dir), blocking=False)]

    config = load_shadow_ensemble_config()

    async def loader(symbol: str, limit: int) -> pd.DataFrame:
        path = cache_dir / _runtime_filename(symbol, "15m")
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
    parser = argparse.ArgumentParser(description="Unified source, deployment, runtime, shadow, data-readiness, and research checks for the OKX signal-only system.")
    parser.add_argument("command", choices=("source", "preflight", "runtime", "shadow", "data", "research", "all"), nargs="?", default="all")
    parser.add_argument("--mode", choices=("observation", "production"), default=os.environ.get("DEPLOYMENT_MODE", "observation"))
    parser.add_argument("--env-file", type=Path, default=PROJECT_ROOT / ".env")
    parser.add_argument("--status-file", type=Path, default=PROJECT_ROOT / "outputs" / "latest_scan_status.json")
    parser.add_argument("--max-age-seconds", type=int, default=int(os.environ.get("HEALTH_MAX_AGE_SECONDS", "1200")))
    parser.add_argument("--max-pending", type=int, default=int(os.environ.get("OUTBOX_MAX_PENDING", "100")))
    parser.add_argument("--candidate", type=Path)
    parser.add_argument("--artifacts", type=Path)
    parser.add_argument("--family-registry", type=Path, default=DEFAULT_FAMILY_REGISTRY)
    parser.add_argument("--archive-failures", type=Path)
    parser.add_argument("--no-auto-archive", action="store_true")
    parser.add_argument("--dataset")
    parser.add_argument("--timeframe")
    parser.add_argument("--data-root", type=Path)
    parser.add_argument("--data-state", type=Path, default=DEFAULT_DATA_STATE)
    parser.add_argument("--data-report", type=Path, default=DEFAULT_DATA_REPORT)
    parser.add_argument("--research-report", type=Path, default=DEFAULT_RESEARCH_REPORT)
    parser.add_argument("--min-research-symbols", type=int)
    parser.add_argument("--min-history-days", type=float)
    parser.add_argument("--min-new-data-days", type=float)
    parser.add_argument("--max-gap-ratio", type=float)
    parser.add_argument("--data-coverage-ratio", type=float)
    parser.add_argument("--mark-researched", action="store_true")
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

    candidate_payload: dict[str, Any] = {}
    if args.candidate is not None and args.candidate.is_file():
        try:
            candidate_payload = _json_mapping(args.candidate)
        except Exception:
            candidate_payload = {}
    base = load_config("base.yaml")
    base_data = base.get("data", {}) if isinstance(base.get("data"), dict) else {}
    candidate_data = candidate_payload.get("data") if isinstance(candidate_payload.get("data"), dict) else {}
    data_gate = candidate_payload.get("data_gate") if isinstance(candidate_payload.get("data_gate"), dict) else {}
    candidate_symbols_raw = candidate_data.get("symbols")
    candidate_symbols = (
        [str(symbol) for symbol in candidate_symbols_raw if str(symbol).strip()]
        if isinstance(candidate_symbols_raw, list)
        else None
    )
    dataset = str(args.dataset or candidate_data.get("dataset") or base_data.get("historical_dataset") or "okx_15m_extended")
    timeframe = str(args.timeframe or candidate_data.get("timeframe") or base_data.get("timeframe") or "15m")
    min_symbols = int(args.min_research_symbols if args.min_research_symbols is not None else data_gate.get("min_symbols", DEFAULT_MIN_RESEARCH_SYMBOLS))
    min_history_days = float(args.min_history_days if args.min_history_days is not None else data_gate.get("min_history_days", DEFAULT_MIN_HISTORY_DAYS))
    min_new_days = float(args.min_new_data_days if args.min_new_data_days is not None else data_gate.get("min_new_days", DEFAULT_MIN_NEW_DATA_DAYS))
    max_gap_ratio = float(args.max_gap_ratio if args.max_gap_ratio is not None else data_gate.get("max_gap_ratio", DEFAULT_MAX_GAP_RATIO))
    coverage_ratio = float(args.data_coverage_ratio if args.data_coverage_ratio is not None else data_gate.get("coverage_ratio", DEFAULT_DATA_COVERAGE_RATIO))

    readiness: DataReadiness | None = None
    if args.command in {"data", "research"}:
        data_results, readiness = run_data_readiness(
            dataset=dataset,
            timeframe=timeframe,
            state_file=args.data_state,
            report_file=args.data_report,
            data_root=args.data_root,
            symbols=candidate_symbols,
            min_symbols=min_symbols,
            min_history_days=min_history_days,
            min_new_days=min_new_days,
            max_gap_ratio=max_gap_ratio,
            coverage_ratio=coverage_ratio,
        )
        results.extend(data_results)

    if args.command == "research":
        if args.candidate is None:
            parser.error("research command requires --candidate")
        archive_root = args.archive_failures or _default_failure_archive_root()
        results.extend(
            run_candidate_gate(
                args.candidate,
                registry_path=args.family_registry,
                archive_root=archive_root,
            )
        )
        if args.artifacts is not None:
            results.extend(run_artifact_gate(args.artifacts, candidate=candidate_payload))
        report_payload = {
            "schema": "okx_research_gate_report_v2",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "version": __version__,
            "candidate": str(args.candidate),
            "artifacts": str(args.artifacts) if args.artifacts is not None else None,
            "dataset": dataset,
            "timeframe": timeframe,
            "data_readiness": asdict(readiness) if readiness is not None else None,
            "checks": [asdict(item) for item in results],
            "ok": not any(item.blocking and not item.ok for item in results),
        }
        _write_json_atomic(args.research_report, report_payload)
        if args.artifacts is not None and args.artifacts.is_dir():
            _write_json_atomic(args.artifacts / "research_gate_report.json", report_payload)
        research_failed = any(item.category == "research" and item.blocking and not item.ok for item in results)
        if research_failed and not args.no_auto_archive:
            destination = archive_failed_research(args.candidate, args.artifacts, archive_root, results)
            results.append(CheckResult("research", "failure_archived", True, str(destination), blocking=False))

    if args.command == "research" and args.mark_researched and readiness is not None and not any(item.blocking and not item.ok for item in results):
        mark_research_data_state(readiness, args.data_state)
        results.append(CheckResult("data", "research_data_state_marked", True, str(args.data_state), blocking=False))

    return _print_results(results, json_output=args.json)


if __name__ == "__main__":
    raise SystemExit(main())
