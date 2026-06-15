from __future__ import annotations

import pandas as pd

from okx_signal_system.risk.model import RiskDecision
from okx_signal_system.signal_quality import SignalCandidate, assign_tiers
from okx_signal_system.strategy.trend_breakout import TradeSignal


def _candidate(symbol: str, score: float) -> SignalCandidate:
    signal = TradeSignal(
        ts=pd.Timestamp("2026-01-01T00:00:00Z"),
        inst_id=symbol,
        side="long",
        entry_ref=100.0,
        stop_loss=98.0,
        take_profit=107.0,
        max_hold_bars=12,
        reason_codes=("TEST",),
        signal_score=score,
        risk_reward_ratio=3.5,
    )
    decision = RiskDecision(
        accepted=True,
        reason=None,
        leverage_cap=3.0,
        qty=1.0,
        risk_amount=100.0,
        leverage_used=3.0,
        signal_score=score,
        risk_reward_ratio=3.5,
    )
    return SignalCandidate(
        signal=signal,
        decision=decision,
        notify_key=f"{symbol}-{score}",
        payload={"signal": {"signal_score": score}},
        health_item={"symbol": symbol, "would_push": True},
        rank_score=score,
        raw_score=score,
    )


def _history(symbol: str, returns: list[float]) -> pd.DataFrame:
    closes = [100.0]
    for value in returns:
        closes.append(closes[-1] * (1.0 + value))
    return pd.DataFrame(
        {
            "ts": pd.date_range("2025-12-31T22:00:00Z", periods=len(closes), freq="15min"),
            "close": closes,
            "is_closed": True,
            "symbol": symbol,
        }
    )


def test_assign_tiers_keeps_all_candidates_and_limits_a_tier() -> None:
    selection = assign_tiers(
        [
            _candidate("LOW-USDT-SWAP", 6.1),
            _candidate("HIGH-USDT-SWAP", 8.2),
            _candidate("MID-USDT-SWAP", 7.0),
        ],
        max_tier_a=2,
    )

    assert [item.inst_id for item in selection.ranked] == [
        "HIGH-USDT-SWAP",
        "MID-USDT-SWAP",
        "LOW-USDT-SWAP",
    ]
    assert [item.tier for item in selection.ranked] == ["A", "A", "B"]
    assert [item.rank for item in selection.ranked] == [1, 2, 3]
    assert len(selection.tier_a) == 2
    assert len(selection.tier_b) == 1


def test_assign_tiers_limits_a_tier_to_one_candidate_per_correlation_group() -> None:
    shared_returns = [0.01, 0.02, -0.01, 0.015, -0.005, 0.02, -0.015, 0.01]
    selection = assign_tiers(
        [
            _candidate("ETH-USDT-SWAP", 8.0),
            _candidate("BTC-USDT-SWAP", 9.0),
        ],
        max_tier_a=2,
        price_history={
            "BTC-USDT-SWAP": _history("BTC-USDT-SWAP", shared_returns),
            "ETH-USDT-SWAP": _history("ETH-USDT-SWAP", shared_returns),
        },
    )

    assert [item.inst_id for item in selection.ranked] == ["BTC-USDT-SWAP", "ETH-USDT-SWAP"]
    assert [item.tier for item in selection.ranked] == ["A", "B"]
    assert selection.ranked[0].correlation_group == selection.ranked[1].correlation_group
    assert len(selection.tier_a) == 1
    assert len(selection.tier_b) == 1


def test_assign_tiers_allows_different_correlation_groups_under_a_tier_cap() -> None:
    shared_returns = [0.01, 0.02, -0.01, 0.015, -0.005, 0.02, -0.015, 0.01]
    different_returns = [-0.02, 0.01, 0.015, -0.005, 0.02, -0.01, 0.005, -0.015]
    selection = assign_tiers(
        [
            _candidate("BTC-USDT-SWAP", 9.0),
            _candidate("SOL-USDT-SWAP", 8.5),
        ],
        max_tier_a=2,
        price_history={
            "BTC-USDT-SWAP": _history("BTC-USDT-SWAP", shared_returns),
            "SOL-USDT-SWAP": _history("SOL-USDT-SWAP", different_returns),
        },
    )

    assert [item.tier for item in selection.ranked] == ["A", "A"]
    assert selection.ranked[0].correlation_group != selection.ranked[1].correlation_group
    assert len(selection.tier_a) == 2


def test_assign_tiers_keeps_correlated_demoted_candidates_in_ranked_output() -> None:
    shared_returns = [0.01, 0.02, -0.01, 0.015, -0.005, 0.02, -0.015, 0.01]
    different_returns = [-0.02, 0.01, 0.015, -0.005, 0.02, -0.01, 0.005, -0.015]
    selection = assign_tiers(
        [
            _candidate("ETH-USDT-SWAP", 8.8),
            _candidate("BTC-USDT-SWAP", 9.0),
            _candidate("SOL-USDT-SWAP", 8.0),
        ],
        max_tier_a=2,
        price_history={
            "BTC-USDT-SWAP": _history("BTC-USDT-SWAP", shared_returns),
            "ETH-USDT-SWAP": _history("ETH-USDT-SWAP", shared_returns),
            "SOL-USDT-SWAP": _history("SOL-USDT-SWAP", different_returns),
        },
    )

    assert [item.inst_id for item in selection.ranked] == [
        "BTC-USDT-SWAP",
        "ETH-USDT-SWAP",
        "SOL-USDT-SWAP",
    ]
    assert [item.tier for item in selection.ranked] == ["A", "B", "A"]
    assert len(selection.ranked) == 3
