from __future__ import annotations

"""Update the isolated forward ledger for the frozen staggered 3x3 momentum cadence.

This thin entrypoint reuses the fixed-cadence shadow runtime. It records
research evidence only and never changes formal signals, notifications,
leverage, approved manifests, accounts, positions, or orders.
"""

from pathlib import Path

import update_momentum_fixed_3d_shadow as runtime

PROJECT_ROOT = Path(__file__).resolve().parents[1]
runtime.PROTOCOL_PATH = (
    PROJECT_ROOT
    / "config"
    / "research_protocols"
    / "momentum_staggered_3x3_refresh_v1.json"
)
runtime.LEDGER_PATH = PROJECT_ROOT / "outputs" / "momentum_staggered_3x3_forward_ledger.json"
runtime.STATUS_PATH = PROJECT_ROOT / "outputs" / "momentum_staggered_3x3_forward_status.json"
runtime.EVIDENCE_DIR = PROJECT_ROOT / "outputs" / "momentum_staggered_3x3_forward_evidence"
runtime.VARIANT = "staggered_3x3_refresh_hysteresis_4_in_6_out"
runtime.ADDITIONAL_CODE_PATHS = [Path(__file__).resolve()]


if __name__ == "__main__":
    raise SystemExit(runtime.main())
