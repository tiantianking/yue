import json
from types import SimpleNamespace

import pandas as pd
import pytest

from okx_signal_system.exchange.candles import okx_candles_to_frame
from okx_signal_system.research.approved_strategy_manifest import (
    build_approved_manifest,
    canonical_sha256,
    write_approved_manifest_atomic,
)
from okx_signal_system.training.startup_quality import (
    _anti_future_checks,
    _select_symbols,
    is_latest_bar_fresh,
    load_selected_strategy_params,
    load_selected_strategy_params_status,
    push_blocking_reasons,
)
from okx_signal_system.strategy.trend_breakout import StrategyParams


def _strict_candidate(params: dict, *, generated_at: str = "2026-01-01T00:00:00+00:00") -> dict:
    return {
        "artifact_type": "strict_research_candidate",
        "generated_at": generated_at,
        "research_run_id": "unit-research-run",
        "dataset": "unit",
        "signal_timeframe": "15m",
        "trend_timeframe": "1h",
        "research_version": "v3.56-strict",
        "research_mode": "FORMAL",
        "promotion_eligible": True,
        "candidate_params": params,
        "candidate_params_sha256": __import__("hashlib").sha256(
            json.dumps(params, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
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


def _rehash_manifest(payload: dict) -> dict:
    body = {key: value for key, value in payload.items() if key not in {"manifest_hash", "manifest_sha256"}}
    digest = canonical_sha256(body)
    payload["manifest_hash"] = digest
    payload["manifest_sha256"] = digest
    return payload


def test_okx_candles_to_frame_accepts_nine_and_ten_field_rows() -> None:
    raw = [
        ["1760000000000", "100", "110", "90", "105", "12", "1200", "1200", "1"],
        ["1760003600000", "105", "112", "101", "108", "9", "900", "900", "1", "extra"],
    ]
    frame = okx_candles_to_frame(raw)
    assert list(frame.columns) == ["ts", "open", "high", "low", "close", "volume", "quote_volume"]
    assert len(frame) == 2
    assert str(frame["ts"].dt.tz) == "UTC"
    assert frame.iloc[0]["volume"] == 12
    assert frame.iloc[0]["quote_volume"] == 1200


def test_load_selected_strategy_params_reads_approved_runtime_manifest(tmp_path) -> None:
    raw_params = {
        "fast_ema": 10,
        "slow_ema": 80,
        "breakout_window": 60,
        "atr_stop_mult": 1.5,
        "take_profit_mult": 3.5,
        "max_hold_bars": 24,
        "atr_window": 14,
    }
    manifest = build_approved_manifest(_strict_candidate(raw_params), approved_at="2026-01-02T00:00:00+00:00")
    write_approved_manifest_atomic(manifest, tmp_path / "runtime" / "approved_strategy_manifest.json")

    params = load_selected_strategy_params(tmp_path)

    assert params.fast_ema == 10
    assert params.slow_ema == 80
    assert params.breakout_window == 60
    assert params.take_profit_mult == 3.5


def test_missing_approved_manifest_blocks_formal_push_but_returns_default_params(tmp_path) -> None:
    status = load_selected_strategy_params_status(tmp_path)

    assert status.ok is False
    assert status.reason == "runtime_manifest_missing"
    assert status.params == StrategyParams()
    assert load_selected_strategy_params(tmp_path) == StrategyParams()


def test_hand_modified_runtime_manifest_fails_hash_validation(tmp_path) -> None:
    raw_params = {
        "fast_ema": 10,
        "slow_ema": 80,
        "breakout_window": 60,
        "atr_stop_mult": 1.5,
        "take_profit_mult": 3.5,
        "max_hold_bars": 24,
        "atr_window": 14,
    }
    path = tmp_path / "runtime" / "approved_strategy_manifest.json"
    write_approved_manifest_atomic(
        build_approved_manifest(_strict_candidate(raw_params), approved_at="2026-01-02T00:00:00+00:00"),
        path,
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["selected_params"]["fast_ema"] = 11
    path.write_text(json.dumps(payload), encoding="utf-8")

    status = load_selected_strategy_params_status(tmp_path)

    assert status.ok is False
    assert status.reason == "runtime_manifest_hash_mismatch"
    assert status.params == StrategyParams()


def test_latest_bar_freshness_blocks_stale_history() -> None:
    now = pd.Timestamp("2026-06-13T12:00:00Z")
    fresh = pd.DataFrame({"ts": [now - pd.Timedelta(hours=2)]})
    stale = pd.DataFrame({"ts": [now - pd.Timedelta(hours=5)]})
    assert is_latest_bar_fresh(fresh, max_lag_hours=3.0, now=now)
    assert not is_latest_bar_fresh(stale, max_lag_hours=3.0, now=now)


def test_select_symbols_preserves_config_order() -> None:
    available = [
        SimpleNamespace(inst_id="ADA-USDT-SWAP"),
        SimpleNamespace(inst_id="BTC-USDT-SWAP"),
        SimpleNamespace(inst_id="ETH-USDT-SWAP"),
    ]
    selected = _select_symbols(available, ["BTC-USDT-SWAP", "ETH-USDT-SWAP", "ADA-USDT-SWAP"], max_symbols=2)
    assert [item.inst_id for item in selected] == ["BTC-USDT-SWAP", "ETH-USDT-SWAP"]


def test_training_performance_warnings_do_not_block_push() -> None:
    reasons = [
        "training_return_not_positive",
        "training_profit_factor_below_1",
        "validation_edge_not_confirmed_by_training",
    ]
    assert push_blocking_reasons(reasons) == []


def test_validation_loss_blocks_push() -> None:
    assert push_blocking_reasons(["validation_profit_factor_below_1"]) == ["validation_profit_factor_below_1"]


def test_manifest_hash_failure_blocks_push() -> None:
    assert push_blocking_reasons(["runtime_manifest_hash_mismatch"]) == ["runtime_manifest_hash_mismatch"]


def test_same_timeframe_trend_has_no_incomplete_higher_bar_failure() -> None:
    checks = _anti_future_checks(signal_timeframe="15m", trend_timeframe="15m")
    assert checks["prior_breakout_excludes_current_bar"]
    assert checks["incomplete_trend_not_tradable"]


@pytest.mark.parametrize(
    ("field", "value", "expected_reason"),
    [
        ("strategy_version", "0.0.0", "runtime_manifest_strategy_version_mismatch"),
        ("research_version", "v3.55-strict", "runtime_manifest_research_version_mismatch"),
        ("research_mode", "NON_FORMAL_SMOKE", "runtime_manifest_research_mode_invalid"),
        ("promotion_eligible", False, "runtime_manifest_promotion_not_eligible"),
        ("operator", "", "runtime_manifest_operator_missing"),
        ("blind_status", "BLIND_SEALED_FAIL", "runtime_manifest_blind_not_sealed_pass"),
    ],
)
def test_semantically_invalid_runtime_manifest_is_rejected_even_with_valid_hash(
    tmp_path, field, value, expected_reason
) -> None:
    params = {
        "fast_ema": 10,
        "slow_ema": 80,
        "breakout_window": 60,
        "atr_stop_mult": 1.5,
        "take_profit_mult": 3.5,
        "max_hold_bars": 24,
        "atr_window": 14,
    }
    payload = build_approved_manifest(
        _strict_candidate(params),
        approved_at="2026-01-02T00:00:00+00:00",
    )
    payload[field] = value
    _rehash_manifest(payload)
    path = tmp_path / "runtime" / "approved_strategy_manifest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")

    status = load_selected_strategy_params_status(tmp_path)

    assert status.ok is False
    assert status.reason == expected_reason
    assert status.params == StrategyParams()


def test_runtime_manifest_rejects_false_blind_evidence_even_after_rehash(tmp_path) -> None:
    params = {
        "fast_ema": 10,
        "slow_ema": 80,
        "breakout_window": 60,
        "atr_stop_mult": 1.5,
        "take_profit_mult": 3.5,
        "max_hold_bars": 24,
        "atr_window": 14,
    }
    payload = build_approved_manifest(
        _strict_candidate(params),
        approved_at="2026-01-02T00:00:00+00:00",
    )
    payload["research_metadata"]["blind_evaluation"]["passed"] = False
    _rehash_manifest(payload)
    path = tmp_path / "runtime" / "approved_strategy_manifest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")

    status = load_selected_strategy_params_status(tmp_path)

    assert status.ok is False
    assert status.reason == "runtime_manifest_blind_not_sealed_pass"
    assert status.params == StrategyParams()


def test_runtime_manifest_rejects_incomplete_research_grid_even_after_rehash(tmp_path) -> None:
    params = {
        "fast_ema": 10,
        "slow_ema": 80,
        "breakout_window": 60,
        "atr_stop_mult": 1.5,
        "take_profit_mult": 3.5,
        "max_hold_bars": 24,
        "atr_window": 14,
    }
    payload = build_approved_manifest(
        _strict_candidate(params),
        approved_at="2026-01-02T00:00:00+00:00",
    )
    payload["research_metadata"]["completed_parameter_cells"] = 0
    _rehash_manifest(payload)
    path = tmp_path / "runtime" / "approved_strategy_manifest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")

    status = load_selected_strategy_params_status(tmp_path)

    assert status.ok is False
    assert status.reason == "runtime_manifest_research_grid_coverage_incomplete"
    assert status.params == StrategyParams()
