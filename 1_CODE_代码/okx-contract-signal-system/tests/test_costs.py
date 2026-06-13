import pandas as pd
import pytest

from okx_signal_system.exchange.okx import okx_place_order_preview
from okx_signal_system.risk.costs import estimate_costs, funding_events_crossed, participation_rate, slippage_bps_for_participation


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


def test_okx_preview_rejects_non_swap() -> None:
    with pytest.raises(ValueError):
        okx_place_order_preview(inst_id="BTC-USDT", side="buy", size=1, price=None, client_order_id="x")
