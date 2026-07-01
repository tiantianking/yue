from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import runtime_check as _runtime_check


def evaluate(
    status: dict[str, Any],
    *,
    mode: str,
    max_age_seconds: int,
    fallback_backfill: dict[str, Any] | None = None,
    authoritative_outbox: dict[str, int] | None = None,
    max_pending: int = 100,
):
    """Backward-compatible evaluator backed only by the runtime checker."""
    return _runtime_check.evaluate_runtime(
        status,
        mode=mode,
        max_age_seconds=max_age_seconds,
        configured=None,
        fallback_backfill=fallback_backfill,
        authoritative_outbox=authoritative_outbox,
        max_pending=max_pending,
    )


evaluate_runtime = _runtime_check.evaluate_runtime


def main(argv: list[str] | None = None) -> int:
    return _runtime_check.main(["runtime", *(argv if argv is not None else sys.argv[1:])])


if __name__ == "__main__":
    raise SystemExit(main())
