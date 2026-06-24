from __future__ import annotations

import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import system_check as _system_check

run_shadow_check = _system_check.run_shadow_check


def main(argv: list[str] | None = None) -> int:
    return _system_check.main(["shadow", *(argv if argv is not None else sys.argv[1:])])


if __name__ == "__main__":
    raise SystemExit(main())
