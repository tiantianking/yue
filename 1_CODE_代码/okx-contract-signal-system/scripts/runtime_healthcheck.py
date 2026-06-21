from __future__ import annotations

import argparse
import json
import os
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class HealthResult:
    name: str
    ok: bool
    detail: str


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


def evaluate(
    status: dict[str, Any],
    *,
    mode: str,
    max_age_seconds: int,
    fallback_backfill: dict[str, Any] | None = None,
    authoritative_outbox: dict[str, int] | None = None,
    max_pending: int = 100,
) -> list[HealthResult]:
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

    websocket_detail = (
        f"connected={websocket.get('connected')} degraded={websocket.get('degraded')} "
        f"reconnect_count={websocket.get('reconnect_count', 0)} last_error={websocket.get('last_error')}"
    )
    backfill_detail = (
        f"all_complete={backfill.get('all_complete')} symbols_checked={backfill.get('symbols_checked')} "
        f"write_failures={backfill.get('write_failures', 0)} latest={backfill.get('expected_latest_closed')}"
    )
    results = [
        HealthResult("status_file_fresh", age_seconds <= max_age_seconds, f"age_seconds={age_seconds:.1f}"),
        HealthResult("runtime_status", status.get("status") == "running", str(status.get("status"))),
        HealthResult("runtime_error", not status.get("error"), str(status.get("error"))),
        HealthResult("websocket_connected", websocket.get("connected") is True, websocket_detail),
        HealthResult("websocket_not_degraded", websocket.get("degraded") is not True, websocket_detail),
        HealthResult("closed_backfill_complete", backfill.get("all_complete") is True, backfill_detail),
        HealthResult("outbox_no_failed", int(outbox.get("failed") or 0) == 0, str(outbox.get("failed") or 0)),
        HealthResult("outbox_no_dead_letter", int(outbox.get("dead_letter") or 0) == 0, str(outbox.get("dead_letter") or 0)),
        HealthResult(
            "outbox_pending_bounded",
            int(outbox.get("pending") or 0) <= max_pending,
            f"pending={int(outbox.get('pending') or 0)} max={max_pending}",
        ),
    ]
    if mode == "production":
        results.extend(
            [
                HealthResult("formal_push_allowed", status.get("push_allowed") is True, str(status.get("push_allowed"))),
                HealthResult("approved_manifest_valid", manifest.get("ok") is True, str(manifest.get("reason"))),
            ]
        )
    return results


def _load_outbox_counts(path: Path) -> dict[str, int]:
    if not path.exists():
        return {}
    try:
        with sqlite3.connect(path) as connection:
            rows = connection.execute(
                "SELECT status, COUNT(*) FROM notification_outbox GROUP BY status"
            ).fetchall()
    except (sqlite3.Error, OSError):
        return {}
    counts = {str(status).lower(): int(count) for status, count in rows}
    return {
        "pending": counts.get("pending", 0),
        "failed": counts.get("failed", 0),
        "in_progress": counts.get("in_progress", 0),
        "sent": counts.get("sent", 0),
        "dead_letter": counts.get("dead_letter", 0),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Check current OKX signal runtime health.")
    parser.add_argument("--status-file", type=Path, default=PROJECT_ROOT / "outputs" / "latest_scan_status.json")
    parser.add_argument("--mode", choices=("observation", "production"), default=os.environ.get("DEPLOYMENT_MODE", "observation"))
    parser.add_argument("--max-age-seconds", type=int, default=int(os.environ.get("HEALTH_MAX_AGE_SECONDS", "1200")))
    parser.add_argument("--max-pending", type=int, default=int(os.environ.get("OUTBOX_MAX_PENDING", "100")))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if not args.status_file.exists():
        payload = {"ok": False, "error": f"status file missing: {args.status_file}"}
        print(json.dumps(payload, ensure_ascii=False) if args.json else payload["error"])
        return 1

    try:
        status = json.loads(args.status_file.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        payload = {"ok": False, "error": f"invalid status file: {exc}"}
        print(json.dumps(payload, ensure_ascii=False) if args.json else payload["error"])
        return 1

    fallback_backfill: dict[str, Any] = {}
    fallback_path = args.status_file.parent / "closed_kline_backfill_status.json"
    if fallback_path.exists():
        try:
            loaded_backfill = json.loads(fallback_path.read_text(encoding="utf-8-sig"))
            if isinstance(loaded_backfill, dict):
                fallback_backfill = loaded_backfill
        except Exception:
            fallback_backfill = {}

    outbox_counts = _load_outbox_counts(args.status_file.parent / "signal_lifecycle.sqlite3")
    results = evaluate(
        status,
        mode=args.mode,
        max_age_seconds=args.max_age_seconds,
        fallback_backfill=fallback_backfill,
        authoritative_outbox=outbox_counts or None,
        max_pending=max(0, args.max_pending),
    )
    failed = [item for item in results if not item.ok]
    if args.json:
        print(json.dumps({"ok": not failed, "mode": args.mode, "checks": [asdict(item) for item in results]}, ensure_ascii=False, indent=2))
    else:
        for item in results:
            print(f"[{'PASS' if item.ok else 'FAIL'}] {item.name}: {item.detail}")
        print("HEALTHY" if not failed else "UNHEALTHY")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
