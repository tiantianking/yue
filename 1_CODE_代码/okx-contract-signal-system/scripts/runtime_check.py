from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from okx_signal_system import __version__
from okx_signal_system.config import env_bool, load_config
from okx_signal_system.runtime_manifest import load_approved_manifest_status


@dataclass(frozen=True)
class CheckResult:
    category: str
    name: str
    ok: bool
    detail: str
    blocking: bool = True


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
        probe = path / ".runtime-check-write-test"
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


def _print_results(results: list[CheckResult], *, json_output: bool) -> int:
    failed = [item for item in results if item.blocking and not item.ok]
    if json_output:
        print(json.dumps({"ok": not failed, "version": __version__, "checks": [asdict(item) for item in results]}, ensure_ascii=False, indent=2))
    else:
        for item in results:
            state = "PASS" if item.ok else ("FAIL" if item.blocking else "WARN")
            print(f"[{state}] {item.category}.{item.name}: {item.detail}")
        print("RUNTIME CHECK PASSED" if not failed else "RUNTIME CHECK FAILED")
    return 0 if not failed else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Lightweight deployment preflight and runtime health checks for the OKX signal-only system.")
    parser.add_argument("command", choices=("preflight", "runtime", "all"), nargs="?", default="all")
    parser.add_argument("--mode", choices=("observation", "production"), default=os.environ.get("DEPLOYMENT_MODE", "observation"))
    parser.add_argument("--env-file", type=Path, default=PROJECT_ROOT / ".env")
    parser.add_argument("--status-file", type=Path, default=PROJECT_ROOT / "outputs" / "latest_scan_status.json")
    parser.add_argument("--max-age-seconds", type=int, default=int(os.environ.get("HEALTH_MAX_AGE_SECONDS", "1200")))
    parser.add_argument("--max-pending", type=int, default=int(os.environ.get("OUTBOX_MAX_PENDING", "100")))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    results: list[CheckResult] = []
    if args.command in {"preflight", "all"}:
        results.extend(run_preflight(args.mode, args.env_file))
    if args.command in {"runtime", "all"}:
        results.extend(
            run_runtime(
                args.status_file,
                mode=args.mode,
                max_age_seconds=args.max_age_seconds,
                max_pending=args.max_pending,
            )
        )
    return _print_results(results, json_output=args.json)


if __name__ == "__main__":
    raise SystemExit(main())
