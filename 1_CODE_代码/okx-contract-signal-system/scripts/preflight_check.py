from __future__ import annotations

import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import system_check as _system_check

load_approved_manifest_status = _system_check.load_approved_manifest_status


def run_preflight(mode: str, env_file: Path):
    """Backward-compatible entry point backed by the unified checker."""
    original = _system_check.load_approved_manifest_status
    _system_check.load_approved_manifest_status = load_approved_manifest_status
    try:
        return _system_check.run_preflight(mode, env_file)
    finally:
        _system_check.load_approved_manifest_status = original


def main(argv: list[str] | None = None) -> int:
    return _system_check.main(["preflight", *(argv if argv is not None else sys.argv[1:])])


if __name__ == "__main__":
    raise SystemExit(main())
