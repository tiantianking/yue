from __future__ import annotations

import pandas as pd

NEAR_BREAKOUT_GAP_PCT = 0.005
NEAR_BREAKOUT_DISTANCE_ATR = 0.3


def breakout_distance_atr(row: pd.Series | None) -> float | None:
    if row is None:
        return None
    try:
        close = float(row.get("close", 0.0))
        atr = float(row.get("atr", 0.0))
        if close <= 0 or atr <= 0:
            return None
        bias = str(row.get("trend_bias", row.get("bias_4h", "flat")))
        if bias == "long":
            breakout_level = float(row.get("breakout_high"))
            if breakout_level <= 0:
                return None
            return max(0.0, (breakout_level - close) / atr)
        elif bias == "short":
            breakout_level = float(row.get("breakout_low"))
            if breakout_level <= 0:
                return None
            return max(0.0, (close - breakout_level) / atr)
    except (TypeError, ValueError):
        return None
    return None


def near_breakout_observation(row: pd.Series | None) -> tuple[str, float, float, float, float] | None:
    if row is None:
        return None
    try:
        close = float(row.get("close", 0.0))
        if close <= 0:
            return None
        bias = str(row.get("trend_bias", row.get("bias_4h", "flat")))
        if bias == "long":
            breakout_level = float(row.get("breakout_high"))
            if breakout_level <= 0:
                return None
            gap_pct = max(0.0, (breakout_level - close) / close)
            distance_atr = breakout_distance_atr(row)
            if distance_atr is not None and 0.0 < distance_atr <= NEAR_BREAKOUT_DISTANCE_ATR:
                return "long", close, breakout_level, gap_pct, distance_atr
        elif bias == "short":
            breakout_level = float(row.get("breakout_low"))
            if breakout_level <= 0:
                return None
            gap_pct = max(0.0, (close - breakout_level) / close)
            distance_atr = breakout_distance_atr(row)
            if distance_atr is not None and 0.0 < distance_atr <= NEAR_BREAKOUT_DISTANCE_ATR:
                return "short", close, breakout_level, gap_pct, distance_atr
    except (TypeError, ValueError):
        return None
    return None
