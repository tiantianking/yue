from __future__ import annotations

import pandas as pd

NEAR_BREAKOUT_GAP_PCT = 0.005


def near_breakout_observation(row: pd.Series | None) -> tuple[str, float, float, float] | None:
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
            if 0.0 < gap_pct <= NEAR_BREAKOUT_GAP_PCT:
                return "long", close, breakout_level, gap_pct
        elif bias == "short":
            breakout_level = float(row.get("breakout_low"))
            if breakout_level <= 0:
                return None
            gap_pct = max(0.0, (close - breakout_level) / close)
            if 0.0 < gap_pct <= NEAR_BREAKOUT_GAP_PCT:
                return "short", close, breakout_level, gap_pct
    except (TypeError, ValueError):
        return None
    return None
