from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class SignalQualityFeatures:
    trend_spread: float | None
    trend_slope: float | None
    trend_alignment_15m_1h: float | None
    breakout_distance_atr: float | None
    candle_close_location: float | None
    volume_percentile: float | None
    atr_percentile: float | None
    stop_distance_percent: float | None
    breakout_range_compression: float | None

    def as_dict(self) -> dict[str, float | None]:
        return asdict(self)


def build_signal_quality_features(
    signal: Any,
    frame: pd.DataFrame,
    *,
    percentile_lookback: int = 100,
    trend_slope_bars: int = 3,
) -> SignalQualityFeatures | None:
    """Build signal-time quality features from closed historical candles only."""

    history = _closed_history_until(frame, getattr(signal, "ts", None))
    if history.empty:
        return None

    row = history.iloc[-1]
    close = _number(row.get("close"))
    atr_value = _number(row.get("atr"))
    side = str(getattr(signal, "side", ""))
    entry_ref = _number(getattr(signal, "entry_ref", None))
    stop_loss = _number(getattr(signal, "stop_loss", None))

    return SignalQualityFeatures(
        trend_spread=_trend_spread(row),
        trend_slope=_trend_slope(history, close, trend_slope_bars),
        trend_alignment_15m_1h=_trend_alignment(row, side),
        breakout_distance_atr=_breakout_distance_atr(row, side, close, atr_value),
        candle_close_location=_candle_close_location(row),
        volume_percentile=_percentile_at_signal(history, "volume", percentile_lookback),
        atr_percentile=_percentile_at_signal(history, "atr", percentile_lookback),
        stop_distance_percent=_stop_distance_percent(entry_ref, stop_loss),
        breakout_range_compression=_breakout_range_compression(row, atr_value),
    )


def build_signal_quality_feature_dict(
    signal: Any,
    frame: pd.DataFrame,
    *,
    percentile_lookback: int = 100,
    trend_slope_bars: int = 3,
) -> dict[str, float | None] | None:
    features = build_signal_quality_features(
        signal,
        frame,
        percentile_lookback=percentile_lookback,
        trend_slope_bars=trend_slope_bars,
    )
    return features.as_dict() if features is not None else None


def _closed_history_until(frame: pd.DataFrame, signal_time: Any) -> pd.DataFrame:
    if frame.empty or "ts" not in frame.columns or signal_time is None:
        return pd.DataFrame()

    df = frame.copy()
    if "is_closed" in df.columns:
        df = df[df["is_closed"].map(_is_closed_value)]
    if df.empty:
        return pd.DataFrame()

    df["ts"] = pd.to_datetime(df["ts"], utc=True, errors="coerce")
    df = df.dropna(subset=["ts"]).sort_values("ts").reset_index(drop=True)
    cutoff = _utc_timestamp(signal_time)
    return df[df["ts"] <= cutoff].reset_index(drop=True)


def _utc_timestamp(value: Any) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        return ts.tz_localize("UTC")
    return ts.tz_convert("UTC")


def _is_closed_value(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip().lower() not in {"", "0", "false", "no"}
    return bool(value)


def _number(value: Any) -> float | None:
    parsed = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.isna(parsed):
        return None
    value = float(parsed)
    return value if np.isfinite(value) else None


def _trend_spread(row: pd.Series) -> float | None:
    ema_fast = _number(row.get("ema_fast"))
    ema_slow = _number(row.get("ema_slow"))
    close = _number(row.get("close"))
    if ema_fast is None or ema_slow is None or close is None or close <= 0:
        return None
    return float((ema_fast - ema_slow) / close)


def _trend_slope(history: pd.DataFrame, close: float | None, bars: int) -> float | None:
    if bars <= 0:
        return None
    column = "trend_ema_fast" if "trend_ema_fast" in history.columns else "ema_fast"
    if column not in history.columns or len(history) <= bars:
        return None

    series = pd.to_numeric(history[column], errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
    if len(series) <= bars:
        return None
    current = float(series.iloc[-1])
    previous = float(series.iloc[-bars - 1])
    denominator = close if close is not None and close > 0 else abs(previous)
    if denominator <= 0:
        return None
    return float((current - previous) / denominator / bars)


def _trend_alignment(row: pd.Series, side: str) -> float | None:
    if side not in {"long", "short"}:
        return None

    signal_bias = _ema_bias(row, "ema_fast", "ema_slow")
    trend_bias = _bias_text(row.get("trend_bias", row.get("bias_4h")))
    if trend_bias is None:
        trend_bias = _ema_bias(row, "trend_ema_fast", "trend_ema_slow")
    if signal_bias is None or trend_bias is None:
        return None
    return 1.0 if signal_bias == trend_bias == side else 0.0


def _ema_bias(row: pd.Series, fast_name: str, slow_name: str) -> str | None:
    fast = _number(row.get(fast_name))
    slow = _number(row.get(slow_name))
    if fast is None or slow is None:
        return None
    if fast > slow:
        return "long"
    if fast < slow:
        return "short"
    return "flat"


def _bias_text(value: Any) -> str | None:
    text = str(value).strip().lower()
    return text if text in {"long", "short", "flat"} else None


def _breakout_distance_atr(
    row: pd.Series,
    side: str,
    close: float | None,
    atr_value: float | None,
) -> float | None:
    if side not in {"long", "short"} or close is None or atr_value is None or atr_value <= 0:
        return None

    if side == "long":
        level = _number(row.get("breakout_high"))
        return float((close - level) / atr_value) if level is not None else None

    level = _number(row.get("breakout_low"))
    return float((level - close) / atr_value) if level is not None else None


def _candle_close_location(row: pd.Series) -> float | None:
    high = _number(row.get("high"))
    low = _number(row.get("low"))
    close = _number(row.get("close"))
    if high is None or low is None or close is None:
        return None
    candle_range = high - low
    if candle_range <= 0:
        return 0.0
    return float(((close - low) - (high - close)) / candle_range)


def _percentile_at_signal(history: pd.DataFrame, column: str, lookback: int) -> float | None:
    if column not in history.columns or lookback <= 0:
        return None
    values = pd.to_numeric(history[column], errors="coerce").replace([np.inf, -np.inf], np.nan).dropna().tail(lookback)
    if values.empty:
        return None
    current = float(values.iloc[-1])
    return float((values <= current).sum() / len(values))


def _stop_distance_percent(entry_ref: float | None, stop_loss: float | None) -> float | None:
    if entry_ref is None or stop_loss is None or entry_ref <= 0:
        return None
    return float(abs(entry_ref - stop_loss) / entry_ref)


def _breakout_range_compression(row: pd.Series, atr_value: float | None) -> float | None:
    high_level = _number(row.get("breakout_high"))
    low_level = _number(row.get("breakout_low"))
    if high_level is None or low_level is None or atr_value is None or atr_value <= 0:
        return None
    breakout_range = high_level - low_level
    if breakout_range <= 0:
        return None
    return float(atr_value / breakout_range)


__all__ = [
    "SignalQualityFeatures",
    "build_signal_quality_feature_dict",
    "build_signal_quality_features",
]
