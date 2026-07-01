from __future__ import annotations

import hashlib
import json

import pytest

from okx_signal_system.research.approved_strategy_manifest import build_approved_manifest
from okx_signal_system.runtime_manifest import ManifestValidationError, validate_approved_manifest


def _strict_candidate(params: dict) -> dict:
    params_hash = hashlib.sha256(
        json.dumps(params, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "artifact_type": "strict_research_candidate",
        "generated_at": "2026-01-01T00:00:00+00:00",
        "research_run_id": "runtime-boundary-test",
        "dataset": "unit",
        "signal_timeframe": "15m",
        "trend_timeframe": "1h",
        "research_version": "v3.56-strict",
        "research_mode": "FORMAL",
        "promotion_eligible": True,
        "candidate_params": params,
        "candidate_params_sha256": params_hash,
        "artifact_hashes": {},
        "research_metadata": {
            "dataset": "unit",
            "signal_timeframe": "15m",
            "trend_timeframe": "1h",
            "research_version": "v3.56-strict",
            "research_mode": "FORMAL",
            "promotion_eligible": True,
            "blind_commitment_verified": True,
            "expected_parameter_combinations": 1,
            "completed_parameter_combinations": 1,
            "expected_parameter_cells": 1,
            "completed_parameter_cells": 1,
            "blind_lock_status": "BLIND_SEALED_PASS",
            "blind_evaluation": {"status": "BLIND_SEALED_PASS", "passed": True},
        },
    }


def test_runtime_validator_accepts_manifest_created_by_local_research() -> None:
    raw_params = {
        "fast_ema": 10,
        "slow_ema": 80,
        "breakout_window": 60,
        "atr_stop_mult": 1.5,
        "take_profit_mult": 3.5,
        "max_hold_bars": 24,
        "atr_window": 14,
    }
    manifest = build_approved_manifest(
        _strict_candidate(raw_params),
        approved_at="2026-01-02T00:00:00+00:00",
    )

    params = validate_approved_manifest(manifest)

    assert params.fast_ema == 10
    assert params.slow_ema == 80
    assert params.breakout_window == 60
    assert params.take_profit_mult == 3.5


def test_runtime_validator_still_rejects_tampered_parameters() -> None:
    raw_params = {
        "fast_ema": 10,
        "slow_ema": 80,
        "breakout_window": 60,
        "atr_stop_mult": 1.5,
        "take_profit_mult": 3.5,
        "max_hold_bars": 24,
        "atr_window": 14,
    }
    manifest = build_approved_manifest(
        _strict_candidate(raw_params),
        approved_at="2026-01-02T00:00:00+00:00",
    )
    manifest["selected_params"]["fast_ema"] = 11

    with pytest.raises(ManifestValidationError, match="runtime_manifest_hash_mismatch"):
        validate_approved_manifest(manifest)
