from __future__ import annotations

"""Batch candidate-factory runner built on the frozen research gates.

The runner discovers only schema-v2 return-blind candidates, evaluates them in
one batch through scripts/system_check.py, keeps per-candidate reports, and
publishes one machine-readable factory status. It never opens PnL by itself,
never changes parameters, never promotes to A-tier, and never executes orders.
"""

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from okx_signal_system.io_atomic import write_text_atomic

SYSTEM_CHECK = PROJECT_ROOT / "scripts" / "system_check.py"
DEFAULT_CANDIDATE_DIR = PROJECT_ROOT / "config" / "research_candidates"
DEFAULT_REPORT_ROOT = PROJECT_ROOT / "outputs" / "candidate_factory"
DEFAULT_STATUS = PROJECT_ROOT / "outputs" / "candidate_factory_status.json"
TERMINAL_STATUS_PREFIXES = ("REJECT_", "ARCHIVED_", "WITHDRAWN_")
TERMINAL_STATUSES = {"FAIL_STOP_NO_RESCUE", "REJECT_AND_ARCHIVE_NO_RESCUE"}


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON object required: {path}")
    return payload


def discover_candidates(root: Path) -> list[tuple[Path, dict[str, Any]]]:
    candidates: list[tuple[Path, dict[str, Any]]] = []
    for path in sorted(root.glob("*.json")):
        if path.name.startswith("PRE_PNL_CANDIDATE_TEMPLATE"):
            continue
        try:
            payload = _read_json(path)
        except Exception:
            continue
        if payload.get("schema") != "okx_pre_pnl_candidate_v2":
            continue
        status = str(payload.get("status", "")).strip().upper()
        if status in TERMINAL_STATUSES or status.startswith(TERMINAL_STATUS_PREFIXES):
            continue
        candidates.append((path, payload))
    return candidates


def _safe_id(value: Any) -> str:
    text = str(value or "unknown_candidate")
    return "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in text)


def _artifact_dir(candidate: dict[str, Any], candidate_path: Path) -> Path | None:
    value = candidate.get("artifacts_dir")
    if not value:
        return None
    path = Path(str(value))
    if not path.is_absolute():
        path = (candidate_path.parent / path).resolve()
    return path


def run_candidate(
    candidate_path: Path,
    candidate: dict[str, Any],
    *,
    report_root: Path,
    archive_root: Path | None,
    auto_archive: bool,
) -> dict[str, Any]:
    candidate_id = _safe_id(candidate.get("candidate_id"))
    report_path = report_root / f"{candidate_id}.json"
    artifacts = _artifact_dir(candidate, candidate_path)
    command = [
        sys.executable,
        str(SYSTEM_CHECK),
        "research",
        "--candidate",
        str(candidate_path),
        "--research-report",
        str(report_path),
        "--json",
    ]
    if artifacts is not None:
        command.extend(["--artifacts", str(artifacts)])
    if archive_root is not None:
        command.extend(["--archive-failures", str(archive_root)])
    if not auto_archive:
        command.append("--no-auto-archive")

    completed = subprocess.run(
        command,
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=900,
        check=False,
    )
    report = _read_json(report_path) if report_path.is_file() else {}
    ok = bool(report.get("ok")) and completed.returncode == 0
    if not ok:
        stage = "FAILED_ARCHIVE" if auto_archive else "FAILED_GATE"
        next_action = "PERMANENTLY_REJECT_NO_PARAMETER_RESCUE"
    elif artifacts is None:
        stage = "PRE_PNL_GATE_PASSED"
        next_action = "RUN_FROZEN_HISTORICAL_ROLLING_VALIDATION"
    else:
        stage = "FORWARD_SHADOW_ADMISSION_READY"
        next_action = "START_SEPARATE_RESEARCH_ONLY_FORWARD_SHADOW"

    failed_checks = [
        str(item.get("name"))
        for item in report.get("checks", [])
        if isinstance(item, dict) and item.get("blocking", True) and not item.get("ok")
    ]
    return {
        "candidate_id": candidate.get("candidate_id"),
        "candidate_file": str(candidate_path),
        "artifacts_dir": str(artifacts) if artifacts is not None else None,
        "stage": stage,
        "next_action": next_action,
        "gate_passed": ok,
        "failed_checks": failed_checks,
        "report_file": str(report_path),
        "return_code": completed.returncode,
        "formal_a": False,
        "automatic_promotion": False,
        "automatic_ordering": False,
        "parameter_rescue_allowed": False,
    }


def run_factory(
    *,
    candidate_dir: Path = DEFAULT_CANDIDATE_DIR,
    report_root: Path = DEFAULT_REPORT_ROOT,
    status_path: Path = DEFAULT_STATUS,
    archive_root: Path | None = None,
    auto_archive: bool = True,
) -> dict[str, Any]:
    report_root.mkdir(parents=True, exist_ok=True)
    discovered = discover_candidates(candidate_dir)
    results = [
        run_candidate(
            path,
            candidate,
            report_root=report_root,
            archive_root=archive_root,
            auto_archive=auto_archive,
        )
        for path, candidate in discovered
    ]
    counts: dict[str, int] = {}
    for result in results:
        stage = str(result["stage"])
        counts[stage] = counts.get(stage, 0) + 1

    payload = {
        "schema": "okx_parallel_candidate_factory_v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "candidate_dir": str(candidate_dir),
        "discovered_candidate_count": len(results),
        "stage_counts": counts,
        "candidates": results,
        "existing_forward_shadows": [
            {
                "candidate_id": "CS84_R6_TOP4_BOTTOM4_FORWARD_SHADOW_V1:original",
                "level": "RESEARCH_SHADOW",
            },
            {
                "candidate_id": "CS84_R6_TOP4_BOTTOM4_FORWARD_SHADOW_V1:hysteresis_4_in_6_out",
                "level": "RESEARCH_SHADOW",
            },
            {
                "candidate_id": "v357-shadow-donchian-slow-plus-vcb-a",
                "level": "RESEARCH_SHADOW",
            },
        ],
        "policy": {
            "batch_gate_order": [
                "family_deduplication",
                "data_readiness",
                "future_leak_scan",
                "parameter_freedom",
                "historical_rolling_validation_when_artifacts_exist",
                "random_time_direction_and_delay_falsification_when_artifacts_exist",
                "parameter_profit_plateau_when_artifacts_exist",
                "base_and_stress_costs_when_artifacts_exist",
                "symbol_month_and_few_trade_concentration_when_artifacts_exist",
                "portfolio_incremental_value_when_artifacts_exist",
            ],
            "failed_candidate_action": "ARCHIVE_AND_NEVER_RESCUE_BY_PARAMETER_TUNING",
            "passed_candidate_action": "START_SEPARATE_RESEARCH_ONLY_FORWARD_SHADOW_AFTER_FULL_GATE",
            "formal_a_allowed": False,
            "automatic_ordering": False,
        },
    }
    status_path.parent.mkdir(parents=True, exist_ok=True)
    write_text_atomic(json.dumps(payload, ensure_ascii=False, indent=2), status_path)
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run all registered return-blind research candidates through the frozen gates.")
    parser.add_argument("--candidate-dir", type=Path, default=DEFAULT_CANDIDATE_DIR)
    parser.add_argument("--report-root", type=Path, default=DEFAULT_REPORT_ROOT)
    parser.add_argument("--status", type=Path, default=DEFAULT_STATUS)
    parser.add_argument("--archive-root", type=Path, default=None)
    parser.add_argument("--no-auto-archive", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = run_factory(
        candidate_dir=args.candidate_dir,
        report_root=args.report_root,
        status_path=args.status,
        archive_root=args.archive_root,
        auto_archive=not args.no_auto_archive,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
