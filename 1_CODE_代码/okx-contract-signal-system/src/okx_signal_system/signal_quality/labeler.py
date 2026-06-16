from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import pandas as pd

from okx_signal_system.strategy.trend_breakout import TradeSignal

from .execution import simulate_signal_execution

LabelOutcome = Literal["TP", "SL", "TIMEOUT"]


@dataclass(frozen=True)
class SignalLabel:
    outcome: LabelOutcome
    final_net_r: float
    mae: float
    mfe: float
    holding_bars: int
    exit_time: pd.Timestamp
    exit_price: float


def label_signal(signal: TradeSignal, future_bars: pd.DataFrame) -> SignalLabel | None:
    """Label a completed historical signal using later closed candles only."""
    result = simulate_signal_execution(signal, future_bars)
    if result is None:
        return None
    return SignalLabel(
        outcome=result.outcome,
        final_net_r=result.final_net_r,
        mae=result.mae,
        mfe=result.mfe,
        holding_bars=result.holding_bars,
        exit_time=result.exit_time,
        exit_price=result.exit_price,
    )


def label_trade_signal(signal: TradeSignal, future_bars: pd.DataFrame) -> SignalLabel | None:
    return label_signal(signal, future_bars)


__all__ = [
    "LabelOutcome",
    "SignalLabel",
    "label_signal",
    "label_trade_signal",
]
