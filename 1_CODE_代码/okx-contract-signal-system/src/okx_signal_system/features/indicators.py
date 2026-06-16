from __future__ import annotations

import numpy as np
import pandas as pd

from okx_signal_system.timeframe import default_trend_timeframe, ratio_bars, timeframe_spec


def ema(series: pd.Series, span: int) -> pd.Series:
    if span <= 0:
        raise ValueError("span must be positive")
    return series.ewm(span=span, adjust=False, min_periods=span).mean()


def true_range(frame: pd.DataFrame) -> pd.Series:
    prev_close = frame["close"].shift(1)
    ranges = pd.concat(
        [
            frame["high"] - frame["low"],
            (frame["high"] - prev_close).abs(),
            (frame["low"] - prev_close).abs(),
        ],
        axis=1,
    )
    return ranges.max(axis=1)


def atr(frame: pd.DataFrame, window: int = 14) -> pd.Series:
    if window <= 0:
        raise ValueError("window must be positive")
    return true_range(frame).rolling(window=window, min_periods=window).mean()


def prior_breakout_levels(frame: pd.DataFrame, window: int) -> pd.DataFrame:
    if window <= 0:
        raise ValueError("window must be positive")
    return pd.DataFrame(
        {
            "breakout_high": frame["high"].shift(1).rolling(window=window, min_periods=window).max(),
            "breakout_low": frame["low"].shift(1).rolling(window=window, min_periods=window).min(),
        },
        index=frame.index,
    )


def volume_features(frame: pd.DataFrame, sma_window: int = 20) -> pd.DataFrame:
    """成交量特征：量比和成交量SMA"""
    df = frame.copy()
    df["volume_sma"] = df["volume"].rolling(window=sma_window, min_periods=sma_window).mean()
    df["vol_ratio"] = df["volume"] / df["volume_sma"]
    return df[["vol_ratio", "volume_sma"]]


def trend_ema_spans(
    fast_ema: int,
    slow_ema: int,
    *,
    signal_timeframe: str,
    trend_timeframe: str,
) -> tuple[int, int]:
    """Scale entry EMA spans to the higher trend timeframe."""
    ratio = ratio_bars(trend_timeframe, signal_timeframe)
    return max(2, int(round(fast_ema / ratio))), max(3, int(round(slow_ema / ratio)))


def detect_extreme_volatility(frame: pd.DataFrame, atr_window: int = 14, threshold_multiplier: float = 3.0) -> pd.Series:
    """检测连续极端波动：最近 N 根 bar 中有 >= M 根 ATR 异常放大"""
    atr_series = atr(frame, atr_window)
    atr_pct = atr_series / frame["close"]
    rolling_extreme = atr_pct.rolling(window=3, min_periods=3).max()
    return rolling_extreme > threshold_multiplier * atr_pct.mean() if atr_pct.mean() > 0 else pd.Series(False, index=frame.index)


def market_regime_features(frame: pd.DataFrame, lookback: int = 100) -> pd.Series:
    close = pd.to_numeric(frame["close"], errors="coerce")
    atr_pct = pd.to_numeric(frame.get("atr_pct"), errors="coerce") if "atr_pct" in frame else pd.Series(np.nan, index=frame.index)
    ema_fast = pd.to_numeric(frame.get("ema_fast"), errors="coerce") if "ema_fast" in frame else pd.Series(np.nan, index=frame.index)
    ema_slow = pd.to_numeric(frame.get("ema_slow"), errors="coerce") if "ema_slow" in frame else pd.Series(np.nan, index=frame.index)
    avg_atr_pct = atr_pct.rolling(lookback, min_periods=20).mean()
    atr_ratio = atr_pct / avg_atr_pct.replace(0, np.nan)
    trend_strength = (ema_fast - ema_slow).abs() / close.replace(0, np.nan)
    is_high_vol = atr_ratio > 1.5
    is_strong_trend = trend_strength > 0.005
    regime = np.select(
        [
            is_high_vol & is_strong_trend,
            (~is_high_vol) & is_strong_trend,
            is_high_vol & (~is_strong_trend),
        ],
        ["high_vol_trend", "low_vol_trend", "high_vol_range"],
        default="low_vol_range",
    )
    regime = pd.Series(regime, index=frame.index)
    regime.loc[atr_pct.isna() | close.isna() | ema_fast.isna() | ema_slow.isna()] = "unknown"
    return regime


def add_1h_features(
    frame: pd.DataFrame,
    *,
    fast_ema: int = 20,
    slow_ema: int = 60,
    breakout_window: int = 40,
    atr_window: int = 14,
) -> pd.DataFrame:
    df = frame.sort_values("ts").reset_index(drop=True).copy()
    df["ema_fast"] = ema(df["close"], fast_ema)
    df["ema_slow"] = ema(df["close"], slow_ema)
    df["atr"] = atr(df, atr_window)
    df["atr_pct"] = df["atr"] / df["close"]
    levels = prior_breakout_levels(df, breakout_window)
    return pd.concat([df, levels], axis=1)


def resample_trend(
    frame: pd.DataFrame,
    *,
    signal_timeframe: str = "1h",
    trend_timeframe: str | None = None,
) -> pd.DataFrame:
    signal_spec = timeframe_spec(signal_timeframe)
    trend_key = trend_timeframe or default_trend_timeframe(signal_spec.key)
    trend_spec = timeframe_spec(trend_key)
    expected_count = ratio_bars(trend_spec.key, signal_spec.key)
    df = frame.sort_values("ts").set_index("ts")
    out = df.resample(trend_spec.pandas_freq, label="right", closed="left").agg(
        {
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
        }
    )
    counts = df["close"].resample(trend_spec.pandas_freq, label="right", closed="left").count()
    out["complete_trend"] = counts == expected_count
    # Backward-compatible alias used by older tests and views.
    out["complete_4h"] = out["complete_trend"]
    out = out.dropna(subset=["open", "high", "low", "close"]).reset_index()
    return out


def resample_4h(frame: pd.DataFrame) -> pd.DataFrame:
    return resample_trend(frame, signal_timeframe="1h", trend_timeframe="4h")


def add_trend_features(
    frame_trend: pd.DataFrame,
    *,
    fast_ema: int = 20,
    slow_ema: int = 60,
    trend_timeframe: str = "4h",
) -> pd.DataFrame:
    df = frame_trend.sort_values("ts").reset_index(drop=True).copy()
    trend_key = timeframe_spec(trend_timeframe).key
    df["trend_timeframe"] = trend_key
    df["trend_ema_fast"] = ema(df["close"], fast_ema)
    df["trend_ema_slow"] = ema(df["close"], slow_ema)
    df["trend_bias"] = np.select(
        [df["trend_ema_fast"] > df["trend_ema_slow"], df["trend_ema_fast"] < df["trend_ema_slow"]],
        ["long", "short"],
        default="flat",
    )
    complete_col = "complete_trend" if "complete_trend" in df.columns else "complete_4h"
    df.loc[~df[complete_col].astype(bool), "trend_bias"] = "flat"

    # Backward-compatible aliases. New strategy code reads the generic names.
    df["ema_4h_fast"] = df["trend_ema_fast"]
    df["ema_4h_slow"] = df["trend_ema_slow"]
    df["bias_4h"] = df["trend_bias"]
    return df


def add_4h_trend(frame_4h: pd.DataFrame, *, fast_ema: int = 20, slow_ema: int = 60) -> pd.DataFrame:
    return add_trend_features(frame_4h, fast_ema=fast_ema, slow_ema=slow_ema, trend_timeframe="4h")


def align_completed_trend_to_base(frame_base: pd.DataFrame, frame_trend_features: pd.DataFrame) -> pd.DataFrame:
    left = frame_base.sort_values("ts").copy()
    right = frame_trend_features.sort_values("ts").copy()
    feature_cols = [
        "ts",
        "trend_bias",
        "trend_ema_fast",
        "trend_ema_slow",
        "trend_timeframe",
        "complete_trend",
        "bias_4h",
        "ema_4h_fast",
        "ema_4h_slow",
        "complete_4h",
    ]
    feature_cols = [col for col in feature_cols if col in right.columns]
    aligned = pd.merge_asof(
        left,
        right[feature_cols],
        on="ts",
        direction="backward",
        allow_exact_matches=True,
    )
    return aligned


def align_completed_4h_to_1h(frame_1h: pd.DataFrame, frame_4h_features: pd.DataFrame) -> pd.DataFrame:
    return align_completed_trend_to_base(frame_1h, frame_4h_features)


def build_feature_frame(
    frame_1h: pd.DataFrame,
    *,
    fast_ema: int = 20,
    slow_ema: int = 60,
    breakout_window: int = 40,
    atr_window: int = 14,
    signal_timeframe: str = "1h",
    trend_timeframe: str | None = None,
) -> pd.DataFrame:
    signal_spec = timeframe_spec(signal_timeframe)
    trend_key = trend_timeframe or default_trend_timeframe(signal_spec.key)
    trend_fast_ema, trend_slow_ema = trend_ema_spans(
        fast_ema,
        slow_ema,
        signal_timeframe=signal_spec.key,
        trend_timeframe=trend_key,
    )
    one_h = add_1h_features(
        frame_1h,
        fast_ema=fast_ema,
        slow_ema=slow_ema,
        breakout_window=breakout_window,
        atr_window=atr_window,
    )
    one_h["signal_timeframe"] = signal_spec.key
    four_h = add_trend_features(
        resample_trend(frame_1h, signal_timeframe=signal_spec.key, trend_timeframe=trend_key),
        fast_ema=trend_fast_ema,
        slow_ema=trend_slow_ema,
        trend_timeframe=trend_key,
    )
    aligned = align_completed_trend_to_base(one_h, four_h)

    # 添加成交量特征
    vol_feats = volume_features(aligned, sma_window=20)
    aligned = pd.concat([aligned, vol_feats], axis=1)
    aligned["market_regime"] = market_regime_features(aligned)

    return aligned
