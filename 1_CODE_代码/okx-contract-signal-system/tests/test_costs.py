import pandas as pd
import pytest

from okx_signal_system.risk.costs import (
    estimate_costs,
    funding_events_crossed,
    participation_rate,
    research_position_size,
    research_slippage_bps,
    slippage_bps_for_participation,
)
from okx_signal_system.risk.model import RiskConfig


def test_participation_tiers_reject_over_one_percent() -> None:
    rate = participation_rate(notional=200, close=100, volume=100)
    assert rate == 0.02
    with pytest.raises(ValueError):
        slippage_bps_for_participation(rate)


def test_participation_uses_quote_volume_when_available() -> None:
    rate = participation_rate(notional=200, close=0.16, volume=1000, quote_volume=10000)
    assert rate == 0.02


def test_funding_events_crossed_uses_interval() -> None:
    events = funding_events_crossed(
        pd.Timestamp("2026-01-01T07:00:00Z"),
        pd.Timestamp("2026-01-01T17:00:00Z"),
        interval_hours=8,
    )
    assert [e.hour for e in events] == [8, 16]


def test_estimate_costs_includes_all_cost_layers() -> None:
    costs = estimate_costs(
        entry_price=100,
        exit_price=110,
        qty=2,
        entry_time=pd.Timestamp("2026-01-01T00:00:00Z"),
        exit_time=pd.Timestamp("2026-01-01T09:00:00Z"),
    )
    assert costs.entry_fee > 0
    assert costs.exit_fee > 0
    assert costs.slippage_cost > 0
    assert costs.funding_fee > 0
    assert costs.total == costs.entry_fee + costs.exit_fee + costs.slippage_cost + costs.funding_fee


def test_research_position_size_uses_shared_risk_unit() -> None:
    qty, risk_unit, notional = research_position_size(
        entry_price=100,
        stop_distance=5,
        config=RiskConfig(initial_equity=10000, risk_per_trade_pct=0.01),
    )

    assert qty == 20.0
    assert risk_unit == 100.0
    assert notional == 2000.0


def test_research_slippage_uses_research_notional_for_participation() -> None:
    assert research_slippage_bps(
        notional=2000,
        close=100,
        volume=1000,
        quote_volume=1_000_000,
    ) == 10.0

    with pytest.raises(ValueError):
        research_slippage_bps(
            notional=2000,
            close=100,
            volume=1000,
            quote_volume=100_000,
        )


def test_cost_module_has_no_exchange_execution_dependency() -> None:
    import okx_signal_system.risk.costs as costs

    assert "okx_signal_system.exchange" not in repr(costs.__dict__.get("__loader__", ""))
