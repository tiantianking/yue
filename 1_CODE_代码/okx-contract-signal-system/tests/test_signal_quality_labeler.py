from __future__ import annotations

import pandas as pd
import pytest

from okx_signal_system.risk.costs import CostConfig, estimate_costs
from okx_signal_system.signal_quality.execution import simulate_signal_execution
from okx_signal_system.signal_quality.labeler import label_signal
from okx_signal_system.strategy.trend_breakout import TradeSignal


def _signal(
    *,
    side: str = "long",
    ts: str = "2026-01-01T00:00:00Z",
    entry_ref: float = 100.0,
    stop_loss: float = 95.0,
    take_profit: float = 110.0,
    max_hold_bars: int = 3,
) -> TradeSignal:
    return TradeSignal(
        ts=pd.Timestamp(ts),
        inst_id="BTC-USDT-SWAP",
        side=side,
        entry_ref=entry_ref,
        stop_loss=stop_loss,
        take_profit=take_profit,
        max_hold_bars=max_hold_bars,
        reason_codes=("TEST",),
        signal_score=8.0,
        risk_reward_ratio=2.0,
    )


def _frame(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def _field(label, name: str):
    if isinstance(label, dict):
        return label[name]
    return getattr(label, name)


def _outcome(label) -> str:
    value = _field(label, "outcome")
    return getattr(value, "value", value)


def _assert_exit_time(label, expected: str) -> None:
    assert pd.Timestamp(_field(label, "exit_time")) == pd.Timestamp(expected)


def _expected_net_r(signal: TradeSignal, exit_price: float, exit_time: str) -> float:
    entry = float(signal.entry_ref)
    stop_dist = abs(entry - float(signal.stop_loss))
    side_mult = 1.0 if signal.side == "long" else -1.0
    costs = estimate_costs(
        entry_price=entry,
        exit_price=exit_price,
        qty=1.0,
        entry_time=pd.Timestamp(signal.ts),
        exit_time=pd.Timestamp(exit_time),
        config=CostConfig(),
        slippage_bps=CostConfig().normal_slippage_bps,
    )
    return (((exit_price - entry) * side_mult) - costs.total) / stop_dist


def test_label_signal_exits_at_take_profit() -> None:
    signal = _signal()
    frame = _frame(
        [
            {
                "ts": pd.Timestamp("2026-01-01T00:15:00Z"),
                "open": 100.0,
                "high": 111.0,
                "low": 98.5,
                "close": 110.5,
                "is_closed": True,
            },
        ]
    )

    label = label_signal(signal, frame)

    assert _outcome(label) == "TP"
    _assert_exit_time(label, "2026-01-01T00:15:00Z")
    assert _field(label, "exit_price") == pytest.approx(110.0)
    assert _field(label, "holding_bars") == 1
    assert _field(label, "final_net_r") == pytest.approx(_expected_net_r(signal, 110.0, "2026-01-01T00:15:00Z"))
    assert _field(label, "mae") == pytest.approx(-0.3)
    assert _field(label, "mfe") == pytest.approx(2.2)


def test_label_signal_exits_at_stop_loss_for_short_signal() -> None:
    signal = _signal(side="short", stop_loss=105.0, take_profit=90.0)
    frame = _frame(
        [
            {
                "ts": pd.Timestamp("2026-01-01T00:15:00Z"),
                "open": 100.0,
                "high": 106.0,
                "low": 98.0,
                "close": 104.0,
                "is_closed": True,
            },
        ]
    )

    label = label_signal(signal, frame)

    assert _outcome(label) == "SL"
    _assert_exit_time(label, "2026-01-01T00:15:00Z")
    assert _field(label, "exit_price") == pytest.approx(105.0)
    assert _field(label, "holding_bars") == 1
    assert _field(label, "final_net_r") == pytest.approx(_expected_net_r(signal, 105.0, "2026-01-01T00:15:00Z"))
    assert _field(label, "mae") == pytest.approx(-1.2)
    assert _field(label, "mfe") == pytest.approx(0.4)


def test_label_signal_times_out_at_max_hold_bars() -> None:
    signal = _signal(max_hold_bars=3)
    frame = _frame(
        [
            {
                "ts": pd.Timestamp("2026-01-01T00:15:00Z"),
                "open": 100.0,
                "high": 102.0,
                "low": 99.0,
                "close": 101.0,
                "is_closed": True,
            },
            {
                "ts": pd.Timestamp("2026-01-01T00:30:00Z"),
                "open": 101.0,
                "high": 103.0,
                "low": 100.0,
                "close": 102.0,
                "is_closed": True,
            },
            {
                "ts": pd.Timestamp("2026-01-01T00:45:00Z"),
                "open": 102.0,
                "high": 104.0,
                "low": 101.0,
                "close": 103.0,
                "is_closed": True,
            },
        ]
    )

    label = label_signal(signal, frame)

    assert _outcome(label) == "TIMEOUT"
    _assert_exit_time(label, "2026-01-01T00:45:00Z")
    assert _field(label, "exit_price") == pytest.approx(103.0)
    assert _field(label, "holding_bars") == 3
    assert _field(label, "final_net_r") == pytest.approx(_expected_net_r(signal, 103.0, "2026-01-01T00:45:00Z"))
    assert _field(label, "mae") == pytest.approx(-0.2)
    assert _field(label, "mfe") == pytest.approx(0.8)


def test_label_signal_uses_stop_loss_when_tp_and_sl_hit_on_same_candle() -> None:
    signal = _signal()
    frame = _frame(
        [
            {
                "ts": pd.Timestamp("2026-01-01T00:15:00Z"),
                "open": 100.0,
                "high": 112.0,
                "low": 94.0,
                "close": 101.0,
                "is_closed": True,
            },
        ]
    )

    label = label_signal(signal, frame)

    assert _outcome(label) == "SL"
    _assert_exit_time(label, "2026-01-01T00:15:00Z")
    assert _field(label, "exit_price") == pytest.approx(95.0)
    assert _field(label, "holding_bars") == 1
    assert _field(label, "final_net_r") == pytest.approx(_expected_net_r(signal, 95.0, "2026-01-01T00:15:00Z"))
    assert _field(label, "mae") == pytest.approx(-1.2)
    assert _field(label, "mfe") == pytest.approx(2.4)


def test_label_signal_only_uses_later_closed_candles() -> None:
    signal = _signal(ts="2026-01-01T00:30:00Z")
    frame = _frame(
        [
            {
                "ts": pd.Timestamp("2026-01-01T00:15:00Z"),
                "open": 100.0,
                "high": 120.0,
                "low": 90.0,
                "close": 95.0,
                "is_closed": True,
            },
            {
                "ts": pd.Timestamp("2026-01-01T00:30:00Z"),
                "open": 100.0,
                "high": 120.0,
                "low": 90.0,
                "close": 101.0,
                "is_closed": True,
            },
            {
                "ts": pd.Timestamp("2026-01-01T00:45:00Z"),
                "open": 101.0,
                "high": 112.0,
                "low": 94.0,
                "close": 95.0,
                "is_closed": False,
            },
            {
                "ts": pd.Timestamp("2026-01-01T01:00:00Z"),
                "open": 101.0,
                "high": 111.0,
                "low": 99.0,
                "close": 110.5,
                "is_closed": True,
            },
        ]
    )

    label = label_signal(signal, frame)

    assert _outcome(label) == "TP"
    _assert_exit_time(label, "2026-01-01T01:00:00Z")
    assert _field(label, "exit_price") == pytest.approx(110.0)
    assert _field(label, "holding_bars") == 1
    assert _field(label, "final_net_r") == pytest.approx(_expected_net_r(signal, 110.0, "2026-01-01T01:00:00Z"))
    assert _field(label, "mae") == pytest.approx(-0.2)
    assert _field(label, "mfe") == pytest.approx(2.2)


def test_label_signal_uses_entry_ref_as_quality_reference() -> None:
    signal = _signal(entry_ref=103.0, stop_loss=98.0, take_profit=108.0)
    frame = _frame(
        [
            {
                "ts": pd.Timestamp("2026-01-01T00:15:00Z"),
                "open": 100.0,
                "high": 104.0,
                "low": 102.0,
                "close": 100.5,
                "is_closed": True,
            },
            {
                "ts": pd.Timestamp("2026-01-01T00:30:00Z"),
                "open": 100.5,
                "high": 109.0,
                "low": 100.0,
                "close": 108.5,
                "is_closed": True,
            },
        ]
    )

    label = label_signal(signal, frame)

    assert _outcome(label) == "TP"
    _assert_exit_time(label, "2026-01-01T00:30:00Z")
    assert _field(label, "exit_price") == pytest.approx(108.0)
    assert _field(label, "final_net_r") == pytest.approx(_expected_net_r(signal, 108.0, "2026-01-01T00:30:00Z"))
    assert _field(label, "mae") == pytest.approx(-0.6)
    assert _field(label, "mfe") == pytest.approx(1.2)


def test_label_signal_matches_execution_simulator_result() -> None:
    signal = _signal()
    frame = _frame(
        [
            {
                "ts": pd.Timestamp("2026-01-01T00:15:00Z"),
                "open": 100.0,
                "high": 103.0,
                "low": 99.0,
                "close": 102.0,
                "is_closed": True,
            },
            {
                "ts": pd.Timestamp("2026-01-01T00:30:00Z"),
                "open": 102.0,
                "high": 112.0,
                "low": 101.0,
                "close": 111.0,
                "is_closed": True,
            },
        ]
    )

    execution = simulate_signal_execution(signal, frame)
    label = label_signal(signal, frame)

    assert execution is not None
    assert label is not None
    assert _outcome(label) == execution.outcome
    assert _field(label, "final_net_r") == execution.final_net_r
    assert _field(label, "mae") == execution.mae
    assert _field(label, "mfe") == execution.mfe
    assert _field(label, "holding_bars") == execution.holding_bars
    assert _field(label, "exit_time") == execution.exit_time
    assert _field(label, "exit_price") == execution.exit_price
