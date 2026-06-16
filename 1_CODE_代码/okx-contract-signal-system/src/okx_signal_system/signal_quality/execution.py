from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import pandas as pd

from okx_signal_system.risk.costs import CostBreakdown, CostConfig, estimate_costs, participation_rate, slippage_bps_for_participation
from okx_signal_system.signal_quality.outcome import SignalOutcomeSimulator
from okx_signal_system.strategy.trend_breakout import TradeSignal

LabelOutcome = Literal["TP", "SL", "TIMEOUT"]


@dataclass(frozen=True)
class SignalExecutionResult:
    outcome: LabelOutcome
    final_net_r: float
    mae: float
    mfe: float
    holding_bars: int
    exit_time: pd.Timestamp
    exit_price: float
    stop_dist: float
    costs: CostBreakdown


def simulate_signal_execution(signal: TradeSignal, future_bars: pd.DataFrame, *, cost_config: CostConfig = CostConfig()) -> SignalExecutionResult | None:
    result = SignalOutcomeSimulator().simulate_signal(
        signal,
        future_bars,
        include_entry_bar=False,
        require_complete_timeout=True,
    )
    if result is None:
        return None

    side_mult = 1.0 if signal.side == "long" else -1.0
    df = _future_closed_bars(signal.ts, future_bars)
    if df.empty or result.entry_idx >= len(df):
        return None
    slippage_bps = _slippage_bps_for_row(df.iloc[result.entry_idx], result.entry_price, cost_config=cost_config)
    costs = estimate_costs(
        entry_price=result.entry_price,
        exit_price=result.exit_price,
        qty=1.0,
        entry_time=_utc_timestamp(result.entry_time),
        exit_time=_utc_timestamp(result.exit_time),
        config=cost_config,
        slippage_bps=slippage_bps,
    )
    final_net_r = float((((result.exit_price - result.entry_price) * side_mult) - costs.total) / result.stop_dist)
    return SignalExecutionResult(
        outcome=result.outcome,
        final_net_r=final_net_r,
        mae=result.mae,
        mfe=result.mfe,
        holding_bars=result.holding_bars,
        exit_time=result.exit_time,
        exit_price=result.exit_price,
        stop_dist=result.stop_dist,
        costs=costs,
    )


def _future_closed_bars(signal_time: pd.Timestamp, frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame()
    required = {"ts", "high", "low", "close"}
    if not required.issubset(frame.columns):
        return pd.DataFrame()

    df = frame.copy()
    if "is_closed" in df.columns:
        df = df[df["is_closed"].map(_is_closed_value)]
    if df.empty:
        return pd.DataFrame()

    df["ts"] = pd.to_datetime(df["ts"], utc=True, errors="coerce")
    for column in ["high", "low", "close"]:
        df[column] = pd.to_numeric(df[column], errors="coerce")
    df = df.dropna(subset=["ts", "high", "low", "close"]).sort_values("ts").reset_index(drop=True)
    start = _utc_timestamp(signal_time)
    return df[df["ts"] > start].reset_index(drop=True)


def _utc_timestamp(value: pd.Timestamp) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        return ts.tz_localize("UTC")
    return ts.tz_convert("UTC")


def _is_closed_value(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip().lower() not in {"", "0", "false", "no"}
    return bool(value)


def _slippage_bps_for_row(row: pd.Series, entry_price: float, *, cost_config: CostConfig) -> float:
    volume = pd.to_numeric(pd.Series([row.get("volume")]), errors="coerce").iloc[0]
    if pd.isna(volume) or float(volume) <= 0:
        return cost_config.normal_slippage_bps
    quote_volume = pd.to_numeric(pd.Series([row.get("quote_volume")]), errors="coerce").iloc[0]
    if pd.isna(quote_volume) or float(quote_volume) <= 0:
        quote_volume = None
    try:
        rate = participation_rate(
            notional=abs(float(entry_price)),
            close=float(entry_price),
            volume=float(volume),
            quote_volume=float(quote_volume) if quote_volume is not None else None,
        )
        return slippage_bps_for_participation(rate, base_bps=cost_config.normal_slippage_bps)
    except Exception:
        return cost_config.normal_slippage_bps
