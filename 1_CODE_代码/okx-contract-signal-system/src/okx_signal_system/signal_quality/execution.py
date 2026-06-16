from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
import pandas as pd

from okx_signal_system.risk.costs import CostBreakdown, CostConfig, estimate_costs, participation_rate, slippage_bps_for_participation
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
    if not signal.accepted or signal.side not in {"long", "short"}:
        return None
    if (
        signal.entry_ref is None
        or signal.stop_loss is None
        or signal.take_profit is None
        or signal.max_hold_bars is None
    ):
        return None

    entry_ref = float(signal.entry_ref)
    stop_loss = float(signal.stop_loss)
    take_profit = float(signal.take_profit)
    max_hold_bars = int(signal.max_hold_bars)
    stop_dist = abs(entry_ref - stop_loss)
    if not all(np.isfinite(value) for value in [entry_ref, stop_loss, take_profit]) or stop_dist <= 0 or max_hold_bars <= 0:
        return None

    df = _future_closed_bars(signal.ts, future_bars)
    if df.empty:
        return None

    side_mult = 1.0 if signal.side == "long" else -1.0
    window = df.iloc[:max_hold_bars].reset_index(drop=True)
    if window.empty:
        return None
    timeout_possible = len(df) >= max_hold_bars
    outcome: LabelOutcome = "TIMEOUT"
    exit_idx = len(window) - 1
    exit_price = float(window.iloc[exit_idx]["close"])
    exit_time = pd.Timestamp(window.iloc[exit_idx]["ts"])

    for idx, row in window.iterrows():
        high = float(row["high"])
        low = float(row["low"])
        if signal.side == "long":
            if low <= stop_loss:
                outcome = "SL"
                exit_idx = int(idx)
                exit_price = stop_loss
                exit_time = pd.Timestamp(row["ts"])
                break
            if high >= take_profit:
                outcome = "TP"
                exit_idx = int(idx)
                exit_price = take_profit
                exit_time = pd.Timestamp(row["ts"])
                break
        else:
            if high >= stop_loss:
                outcome = "SL"
                exit_idx = int(idx)
                exit_price = stop_loss
                exit_time = pd.Timestamp(row["ts"])
                break
            if low <= take_profit:
                outcome = "TP"
                exit_idx = int(idx)
                exit_price = take_profit
                exit_time = pd.Timestamp(row["ts"])
                break

    if outcome == "TIMEOUT" and not timeout_possible:
        return None

    observed = window.iloc[: exit_idx + 1]
    if signal.side == "long":
        mfe = float((observed["high"].max() - entry_ref) / stop_dist)
        mae = float((observed["low"].min() - entry_ref) / stop_dist)
    else:
        mfe = float((entry_ref - observed["low"].min()) / stop_dist)
        mae = float((entry_ref - observed["high"].max()) / stop_dist)

    slippage_bps = _slippage_bps_for_row(window.iloc[0], entry_ref, cost_config=cost_config)
    costs = estimate_costs(
        entry_price=entry_ref,
        exit_price=exit_price,
        qty=1.0,
        entry_time=_utc_timestamp(signal.ts),
        exit_time=_utc_timestamp(exit_time),
        config=cost_config,
        slippage_bps=slippage_bps,
    )
    final_net_r = float((((exit_price - entry_ref) * side_mult) - costs.total) / stop_dist)
    return SignalExecutionResult(
        outcome=outcome,
        final_net_r=final_net_r,
        mae=mae,
        mfe=mfe,
        holding_bars=int(exit_idx) + 1,
        exit_time=exit_time,
        exit_price=float(exit_price),
        stop_dist=stop_dist,
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
