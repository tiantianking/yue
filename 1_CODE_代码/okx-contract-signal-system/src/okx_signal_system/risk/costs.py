from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

import pandas as pd


@dataclass(frozen=True)
class CostConfig:
    taker_fee_rate: float = 0.0006
    normal_slippage_bps: float = 5.0
    stress_slippage_bps: float = 10.0
    funding_rate: float = 0.0001
    funding_interval_hours: int = 8


@dataclass(frozen=True)
class CostBreakdown:
    entry_fee: float
    exit_fee: float
    slippage_cost: float
    funding_fee: float

    @property
    def total(self) -> float:
        return self.entry_fee + self.exit_fee + self.slippage_cost + self.funding_fee


def participation_rate(*, notional: float, close: float, volume: float, quote_volume: float | None = None) -> float:
    denominator = quote_volume if quote_volume is not None and pd.notna(quote_volume) and quote_volume > 0 else close * volume
    if denominator <= 0:
        return float("inf")
    return notional / denominator


def slippage_bps_for_participation(rate: float, *, base_bps: float = 5.0) -> float:
    if rate <= 0.001:
        return base_bps
    if rate <= 0.005:
        return base_bps + 5.0
    if rate <= 0.01:
        return base_bps + 10.0
    raise ValueError("participation rate exceeds 1 percent")


def funding_events_crossed(
    entry_time: pd.Timestamp,
    exit_time: pd.Timestamp,
    *,
    interval_hours: int = 8,
) -> list[pd.Timestamp]:
    if exit_time <= entry_time:
        return []
    entry = pd.Timestamp(entry_time).tz_convert("UTC") if pd.Timestamp(entry_time).tzinfo else pd.Timestamp(entry_time).tz_localize("UTC")
    exit_ = pd.Timestamp(exit_time).tz_convert("UTC") if pd.Timestamp(exit_time).tzinfo else pd.Timestamp(exit_time).tz_localize("UTC")
    midnight = entry.normalize()
    step = timedelta(hours=interval_hours)
    current = midnight
    while current <= entry:
        current += step
    events = []
    while current <= exit_:
        events.append(current)
        current += step
    return events


def estimate_costs(
    *,
    entry_price: float,
    exit_price: float,
    qty: float,
    entry_time: pd.Timestamp,
    exit_time: pd.Timestamp,
    config: CostConfig = CostConfig(),
    slippage_bps: float | None = None,
) -> CostBreakdown:
    notional_entry = abs(entry_price * qty)
    notional_exit = abs(exit_price * qty)
    entry_fee = notional_entry * config.taker_fee_rate
    exit_fee = notional_exit * config.taker_fee_rate
    slip_rate = (config.normal_slippage_bps if slippage_bps is None else slippage_bps) / 10000
    slippage_cost = notional_entry * slip_rate + notional_exit * slip_rate
    events = funding_events_crossed(entry_time, exit_time, interval_hours=config.funding_interval_hours)
    avg_position_value = (notional_entry + notional_exit) / 2
    funding_fee = len(events) * avg_position_value * config.funding_rate
    return CostBreakdown(entry_fee, exit_fee, slippage_cost, funding_fee)
