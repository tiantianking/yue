from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import pandas as pd

Side = Literal["long", "short", "flat"]


@dataclass(frozen=True)
class StrategyParams:
    fast_ema: int = 20
    slow_ema: int = 60
    breakout_window: int = 40
    atr_stop_mult: float = 2.0
    take_profit_mult: float = 2.0
    max_hold_bars: int = 48
    atr_window: int = 14


@dataclass(frozen=True)
class TradeSignal:
    ts: pd.Timestamp
    inst_id: str
    side: Side
    entry_ref: float | None
    stop_loss: float | None
    take_profit: float | None
    max_hold_bars: int | None
    reason_codes: tuple[str, ...]
    reject_reason: str | None = None

    @property
    def accepted(self) -> bool:
        return self.side in {"long", "short"} and self.reject_reason is None


def build_signal(row: pd.Series, *, inst_id: str, params: StrategyParams = StrategyParams()) -> TradeSignal:
    ts = pd.Timestamp(row["ts"])
    close = float(row["close"])
    atr = row.get("atr")
    bias = row.get("bias_4h", "flat")
    high_level = row.get("breakout_high")
    low_level = row.get("breakout_low")

    if pd.isna(atr) or atr <= 0:
        return TradeSignal(ts, inst_id, "flat", None, None, None, None, ("ATR_MISSING",), "atr_missing")
    if bias not in {"long", "short"}:
        return TradeSignal(ts, inst_id, "flat", None, None, None, None, ("4H_FLAT",), "flat_4h_bias")
    if pd.isna(high_level) or pd.isna(low_level):
        return TradeSignal(ts, inst_id, "flat", None, None, None, None, ("BREAKOUT_MISSING",), "breakout_missing")

    if bias == "long" and close > float(high_level):
        stop_dist = float(atr) * params.atr_stop_mult
        return TradeSignal(
            ts=ts,
            inst_id=inst_id,
            side="long",
            entry_ref=close,
            stop_loss=close - stop_dist,
            take_profit=close + stop_dist * params.take_profit_mult,
            max_hold_bars=params.max_hold_bars,
            reason_codes=("4H_TREND_LONG", "1H_BREAKOUT_UP", "ATR_OK"),
        )
    if bias == "short" and close < float(low_level):
        stop_dist = float(atr) * params.atr_stop_mult
        return TradeSignal(
            ts=ts,
            inst_id=inst_id,
            side="short",
            entry_ref=close,
            stop_loss=close + stop_dist,
            take_profit=close - stop_dist * params.take_profit_mult,
            max_hold_bars=params.max_hold_bars,
            reason_codes=("4H_TREND_SHORT", "1H_BREAKOUT_DOWN", "ATR_OK"),
        )
    return TradeSignal(ts, inst_id, "flat", None, None, None, None, ("NO_BREAKOUT",), "no_breakout")


def generate_signals(features: pd.DataFrame, *, inst_id: str, params: StrategyParams = StrategyParams()) -> list[TradeSignal]:
    return [build_signal(row, inst_id=inst_id, params=params) for _, row in features.iterrows()]
