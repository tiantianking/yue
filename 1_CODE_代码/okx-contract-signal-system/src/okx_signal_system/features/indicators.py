from __future__ import annotations

import numpy as np
import pandas as pd


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


def detect_extreme_volatility(frame: pd.DataFrame, atr_window: int = 14, threshold_multiplier: float = 3.0) -> pd.Series:
    """检测连续极端波动：最近 N 根 bar 中有 >= M 根 ATR 异常放大"""
    atr = atr(frame, atr_window)
    atr_pct = atr / frame["close"]
    rolling_extreme = atr_pct.rolling(window=3, min_periods=3).max()
    return rolling_extreme > threshold_multiplier * atr_pct.mean() if atr_pct.mean() > 0 else pd.Series(False, index=frame.index)


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


def resample_4h(frame: pd.DataFrame) -> pd.DataFrame:
    df = frame.sort_values("ts").set_index("ts")
    out = df.resample("4h", label="right", closed="right").agg(
        {
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
        }
    )
    counts = df["close"].resample("4h", label="right", closed="right").count()
    out["complete_4h"] = counts == 4
    out = out.dropna(subset=["open", "high", "low", "close"]).reset_index()
    return out


def add_4h_trend(frame_4h: pd.DataFrame, *, fast_ema: int = 20, slow_ema: int = 60) -> pd.DataFrame:
    df = frame_4h.sort_values("ts").reset_index(drop=True).copy()
    df["ema_4h_fast"] = ema(df["close"], fast_ema)
    df["ema_4h_slow"] = ema(df["close"], slow_ema)
    df["bias_4h"] = np.select(
        [df["ema_4h_fast"] > df["ema_4h_slow"], df["ema_4h_fast"] < df["ema_4h_slow"]],
        ["long", "short"],
        default="flat",
    )
    df.loc[~df["complete_4h"], "bias_4h"] = "flat"
    return df


def align_completed_4h_to_1h(frame_1h: pd.DataFrame, frame_4h_features: pd.DataFrame) -> pd.DataFrame:
    left = frame_1h.sort_values("ts").copy()
    right = frame_4h_features.sort_values("ts").copy()
    feature_cols = ["ts", "bias_4h", "ema_4h_fast", "ema_4h_slow", "complete_4h"]
    aligned = pd.merge_asof(
        left,
        right[feature_cols],
        on="ts",
        direction="backward",
        allow_exact_matches=True,
    )
    return aligned


def build_feature_frame(
    frame_1h: pd.DataFrame,
    *,
    fast_ema: int = 20,
    slow_ema: int = 60,
    breakout_window: int = 40,
    atr_window: int = 14,
) -> pd.DataFrame:
    one_h = add_1h_features(
        frame_1h,
        fast_ema=fast_ema,
        slow_ema=slow_ema,
        breakout_window=breakout_window,
        atr_window=atr_window,
    )
    four_h = add_4h_trend(resample_4h(frame_1h), fast_ema=fast_ema, slow_ema=slow_ema)
    aligned = align_completed_4h_to_1h(one_h, four_h)

    # 添加成交量特征
    vol_feats = volume_features(aligned, sma_window=20)
    aligned = pd.concat([aligned, vol_feats], axis=1)

    return aligned
