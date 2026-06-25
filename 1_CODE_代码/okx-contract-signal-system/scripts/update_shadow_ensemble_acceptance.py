from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from okx_signal_system.research.shadow_ensemble_acceptance import write_acceptance_outputs


def refresh_shadow_runtime() -> dict[str, object]:
    completed = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "system_check.py"),
            "shadow",
            "--write-shadow-output",
            "--json",
        ],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=600,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            "shadow ensemble refresh failed\n"
            f"stdout:\n{completed.stdout}\n"
            f"stderr:\n{completed.stderr}"
        )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        payload = {"ok": True, "stdout": completed.stdout.strip()}
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Refresh the frozen shadow ensemble and adapt its non-warmup ledger for parallel acceptance."
    )
    parser.add_argument(
        "--skip-shadow-refresh",
        action="store_true",
        help="Only adapt the current SQLite ledger without running a new shadow scan.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    refresh = None if args.skip_shadow_refresh else refresh_shadow_runtime()
    result = write_acceptance_outputs()
    print(
        json.dumps(
            {
                "shadow_refresh": refresh,
                "acceptance_adapter": result,
                "automatic_ordering": False,
                "automatic_promotion": False,
                "production_effect": "NONE",
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
