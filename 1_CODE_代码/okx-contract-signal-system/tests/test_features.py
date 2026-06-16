import pandas as pd
import pytest

from tests._integration import require_lightweight_history
from okx_signal_system.data.loader import load_symbol_file
from okx_signal_system.features.indicators import (
    add_1h_features,
    align_completed_4h_to_1h,
    build_feature_frame,
    detect_extreme_volatility,
    prior_breakout_levels,
    resample_4h,
    resample_trend,
    trend_ema_spans,
)


def sample_frame() -> pd.DataFrame:
    history = require_lightweight_history("okx_1h_extended", "BTC_USDT_USDT_1h.parquet")
    return load_symbol_file(history / "BTC_USDT_USDT_1h.parquet").frame.head(300)


def test_prior_breakout_excludes_current_bar() -> None:
    frame = pd.DataFrame(
        {
            "high": [10, 11, 12, 50],
            "low": [8, 7, 6, 1],
        }
    )
    levels = prior_breakout_levels(frame, 3)
    assert levels.loc[3, "breakout_high"] == 12
    assert levels.loc[3, "breakout_low"] == 6


@pytest.mark.integration
def test_resample_4h_marks_complete_bars() -> None:
    four_h = resample_4h(sample_frame())
    assert {"ts", "open", "high", "low", "close", "volume", "complete_4h"}.issubset(four_h.columns)
    assert four_h["complete_4h"].dtype == bool


def test_resample_trend_uses_left_closed_start_bars() -> None:
    frame = pd.DataFrame(
        {
            "ts": pd.date_range("2026-01-01T00:00:00Z", periods=5, freq="15min"),
            "open": [10, 20, 30, 40, 999],
            "high": [11, 21, 31, 41, 999],
            "low": [9, 19, 29, 39, 999],
            "close": [15, 25, 35, 45, 999],
            "volume": [1, 2, 3, 4, 999],
        }
    )

    hourly = resample_trend(frame, signal_timeframe="15m", trend_timeframe="1h")
    first_hour = hourly[hourly["ts"] == pd.Timestamp("2026-01-01T01:00:00Z")].iloc[0]

    assert first_hour["open"] == 10
    assert first_hour["high"] == 41
    assert first_hour["low"] == 9
    assert first_hour["close"] == 45
    assert first_hour["volume"] == 10
    assert bool(first_hour["complete_trend"]) is True


@pytest.mark.integration
def test_align_4h_uses_last_completed_value() -> None:
    frame = sample_frame()
    one_h = add_1h_features(frame)
    four_h = resample_4h(frame)
    four_h["bias_4h"] = [f"b{i}" for i in range(len(four_h))]
    four_h["ema_4h_fast"] = range(len(four_h))
    four_h["ema_4h_slow"] = range(len(four_h))
    aligned = align_completed_4h_to_1h(one_h, four_h)
    row = aligned[aligned["ts"] == four_h.loc[5, "ts"]].iloc[0]
    assert row["bias_4h"] == "b5"


@pytest.mark.integration
def test_build_feature_frame_contains_required_features() -> None:
    features = build_feature_frame(sample_frame())
    assert {"ema_fast", "ema_slow", "atr", "breakout_high", "breakout_low", "bias_4h"}.issubset(features.columns)


def test_build_feature_frame_supports_15m_signal_and_1h_trend() -> None:
    periods = 420
    frame = pd.DataFrame(
        {
            "ts": pd.date_range("2026-01-01", periods=periods, freq="15min", tz="UTC"),
            "open": [100 + i * 0.02 for i in range(periods)],
            "high": [101 + i * 0.02 for i in range(periods)],
            "low": [99 + i * 0.02 for i in range(periods)],
            "close": [100.5 + i * 0.02 for i in range(periods)],
            "volume": [1000.0] * periods,
        }
    )
    features = build_feature_frame(frame, signal_timeframe="15m", trend_timeframe="1h")
    assert {"trend_bias", "trend_timeframe", "complete_trend", "signal_timeframe"}.issubset(features.columns)
    assert features["signal_timeframe"].dropna().iloc[-1] == "15m"
    assert features["trend_timeframe"].dropna().iloc[-1] == "1h"


def test_trend_ema_spans_scale_15m_params_to_1h() -> None:
    assert trend_ema_spans(120, 720, signal_timeframe="15m", trend_timeframe="1h") == (30, 180)


def test_detect_extreme_volatility_does_not_use_future_bars() -> None:
    def frame_from_ranges(ranges: list[float]) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "high": [100.0 + value / 2 for value in ranges],
                "low": [100.0 - value / 2 for value in ranges],
                "close": [100.0] * len(ranges),
            }
        )

    prefix_ranges = [1.0, 1.0, 1.0, 8.0, 8.0, 8.0]
    prefix_result = detect_extreme_volatility(
        frame_from_ranges(prefix_ranges),
        atr_window=2,
        threshold_multiplier=1.5,
    )
    full_result = detect_extreme_volatility(
        frame_from_ranges(prefix_ranges + [200.0, 200.0, 200.0]),
        atr_window=2,
        threshold_multiplier=1.5,
    )

    pd.testing.assert_series_equal(prefix_result, full_result.iloc[: len(prefix_ranges)])
    assert bool(prefix_result.iloc[4]) is True
