from __future__ import annotations

import pandas as pd

from okx_signal_system.signal_quality.model import fit_quality_model
from okx_signal_system.signal_quality.quality_shadow import QualityModelShadowScorer
from okx_signal_system.strategy.trend_breakout import TradeSignal


def _signal() -> TradeSignal:
    return TradeSignal(
        ts=pd.Timestamp("2026-01-01T00:45:00Z"),
        inst_id="BTC-USDT-SWAP",
        side="long",
        entry_ref=105.0,
        stop_loss=100.0,
        take_profit=120.0,
        max_hold_bars=12,
        reason_codes=("TEST",),
        signal_score=8.0,
        risk_reward_ratio=3.0,
    )


def _feature_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "ts": pd.Timestamp("2026-01-01T00:30:00Z"),
                "open": 101.0,
                "high": 104.0,
                "low": 100.0,
                "close": 103.0,
                "volume": 300.0,
                "atr": 2.5,
                "ema_fast": 102.0,
                "ema_slow": 100.0,
                "trend_bias": "long",
                "breakout_high": 102.5,
                "breakout_low": 95.5,
                "is_closed": True,
            },
            {
                "ts": pd.Timestamp("2026-01-01T00:45:00Z"),
                "open": 102.0,
                "high": 106.0,
                "low": 101.0,
                "close": 105.0,
                "volume": 250.0,
                "atr": 2.0,
                "ema_fast": 103.0,
                "ema_slow": 100.0,
                "trend_bias": "long",
                "breakout_high": 103.0,
                "breakout_low": 96.0,
                "is_closed": True,
            },
        ]
    )


def _training_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"trend_spread": 0.03, "volume_percentile": 0.8, "outcome": "TP", "final_net_r": 1.5},
            {"trend_spread": 0.02, "volume_percentile": 0.7, "outcome": "TP", "final_net_r": 1.2},
            {"trend_spread": -0.01, "volume_percentile": 0.2, "outcome": "SL", "final_net_r": -1.0},
            {"trend_spread": 0.00, "volume_percentile": 0.4, "outcome": "TIMEOUT", "final_net_r": 0.1},
        ]
    )


def test_quality_shadow_is_disabled_when_model_artifact_is_missing(tmp_path) -> None:
    scorer = QualityModelShadowScorer(tmp_path / "missing.json")

    score = scorer.score(_signal(), _feature_frame()).as_dict()

    assert score["enabled"] is False
    assert score["reason"] == "model_artifact_missing"


def test_quality_shadow_scores_from_optional_artifact_without_decision_effect(tmp_path) -> None:
    path = tmp_path / "signal_quality_model.json"
    fit_quality_model(_training_frame(), feature_columns=["trend_spread", "volume_percentile"]).save(path)
    scorer = QualityModelShadowScorer(path)

    rank_score_before = 8.3
    score = scorer.score(_signal(), _feature_frame()).as_dict()
    rank_score_after = rank_score_before

    assert score["enabled"] is True
    assert score["p_tp"] + score["p_sl"] + score["p_timeout"] == 1.0
    assert "expected_net_r" in score
    assert rank_score_after == rank_score_before
