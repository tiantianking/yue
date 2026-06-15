from __future__ import annotations

import pandas as pd
import pytest

from okx_signal_system.signal_quality.feature_builder import build_signal_quality_feature_dict
from okx_signal_system.strategy.trend_breakout import TradeSignal


def _signal(ts: str = "2026-01-01T00:45:00Z") -> TradeSignal:
    return TradeSignal(
        ts=pd.Timestamp(ts),
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
                "ts": pd.Timestamp("2026-01-01T00:00:00Z"),
                "open": 99.0,
                "high": 101.0,
                "low": 98.0,
                "close": 100.0,
                "volume": 100.0,
                "atr": 1.0,
                "ema_fast": 100.0,
                "ema_slow": 99.0,
                "trend_bias": "long",
                "trend_ema_fast": 100.0,
                "trend_ema_slow": 98.0,
                "breakout_high": 102.0,
                "breakout_low": 95.0,
                "is_closed": True,
            },
            {
                "ts": pd.Timestamp("2026-01-01T00:15:00Z"),
                "open": 100.0,
                "high": 102.0,
                "low": 99.0,
                "close": 101.0,
                "volume": 200.0,
                "atr": 1.5,
                "ema_fast": 101.0,
                "ema_slow": 99.5,
                "trend_bias": "long",
                "trend_ema_fast": 101.0,
                "trend_ema_slow": 98.5,
                "breakout_high": 102.0,
                "breakout_low": 95.0,
                "is_closed": True,
            },
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
                "trend_ema_fast": 102.0,
                "trend_ema_slow": 99.0,
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
                "trend_ema_fast": 103.0,
                "trend_ema_slow": 99.5,
                "breakout_high": 103.0,
                "breakout_low": 96.0,
                "is_closed": True,
            },
        ]
    )


def test_signal_quality_features_ignore_future_rows() -> None:
    frame = _feature_frame()
    future = pd.DataFrame(
        [
            {
                "ts": pd.Timestamp("2026-01-01T01:00:00Z"),
                "open": 500.0,
                "high": 800.0,
                "low": 100.0,
                "close": 700.0,
                "volume": 100000.0,
                "atr": 100.0,
                "ema_fast": 700.0,
                "ema_slow": 100.0,
                "trend_bias": "short",
                "trend_ema_fast": 100.0,
                "trend_ema_slow": 700.0,
                "breakout_high": 900.0,
                "breakout_low": 50.0,
                "is_closed": True,
            },
            {
                "ts": pd.Timestamp("2026-01-01T01:15:00Z"),
                "open": 500.0,
                "high": 800.0,
                "low": 100.0,
                "close": 700.0,
                "volume": 100000.0,
                "atr": 100.0,
                "ema_fast": 700.0,
                "ema_slow": 100.0,
                "trend_bias": "short",
                "trend_ema_fast": 100.0,
                "trend_ema_slow": 700.0,
                "breakout_high": 900.0,
                "breakout_low": 50.0,
                "is_closed": False,
            },
        ]
    )

    baseline = build_signal_quality_feature_dict(_signal(), frame)
    with_future = build_signal_quality_feature_dict(_signal(), pd.concat([frame, future], ignore_index=True))

    assert with_future == baseline
    assert baseline["trend_spread"] == pytest.approx((103.0 - 100.0) / 105.0)
    assert baseline["trend_slope"] == pytest.approx((103.0 - 100.0) / 105.0 / 3.0)
    assert baseline["trend_alignment_15m_1h"] == 1.0
    assert baseline["breakout_distance_atr"] == pytest.approx((105.0 - 103.0) / 2.0)
    assert baseline["candle_close_location"] == pytest.approx(0.6)
    assert baseline["volume_percentile"] == pytest.approx(0.75)
    assert baseline["atr_percentile"] == pytest.approx(0.75)
    assert baseline["stop_distance_percent"] == pytest.approx(5.0 / 105.0)
    assert baseline["breakout_range_compression"] == pytest.approx(2.0 / 7.0)


def test_signal_quality_features_are_prefix_invariant() -> None:
    frame = _feature_frame()
    extended = pd.concat(
        [
            frame,
            pd.DataFrame(
                [
                    {
                        "ts": pd.Timestamp("2026-01-01T01:00:00Z"),
                        "open": 106.0,
                        "high": 109.0,
                        "low": 105.0,
                        "close": 108.0,
                        "volume": 600.0,
                        "atr": 3.0,
                        "ema_fast": 106.0,
                        "ema_slow": 101.0,
                        "trend_bias": "long",
                        "trend_ema_fast": 105.0,
                        "trend_ema_slow": 100.0,
                        "breakout_high": 107.0,
                        "breakout_low": 98.0,
                        "is_closed": True,
                    },
                ]
            ),
        ],
        ignore_index=True,
    )

    from_prefix = build_signal_quality_feature_dict(_signal(), frame)
    from_extended = build_signal_quality_feature_dict(_signal(), extended)

    assert from_extended == from_prefix


def test_signal_quality_features_tolerate_missing_optional_columns() -> None:
    frame = pd.DataFrame(
        [
            {
                "ts": pd.Timestamp("2026-01-01T00:30:00Z"),
                "open": 101.0,
                "high": 104.0,
                "low": 100.0,
                "close": 103.0,
                "is_closed": True,
            },
            {
                "ts": pd.Timestamp("2026-01-01T00:45:00Z"),
                "open": 102.0,
                "high": 106.0,
                "low": 101.0,
                "close": 105.0,
                "is_closed": True,
            },
        ]
    )

    features = build_signal_quality_feature_dict(_signal(), frame)

    assert features is not None
    assert features["candle_close_location"] == pytest.approx(0.6)
    assert features["stop_distance_percent"] == pytest.approx(5.0 / 105.0)
    assert features["trend_spread"] is None
    assert features["trend_slope"] is None
    assert features["trend_alignment_15m_1h"] is None
    assert features["breakout_distance_atr"] is None
    assert features["volume_percentile"] is None
    assert features["atr_percentile"] is None
    assert features["breakout_range_compression"] is None
