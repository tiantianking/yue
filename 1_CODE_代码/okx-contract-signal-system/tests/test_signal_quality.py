from __future__ import annotations

from dataclasses import replace

import pandas as pd

from okx_signal_system.risk.model import RiskDecision
from okx_signal_system.signal_quality import SignalCandidate, assign_tiers
from okx_signal_system.signal_quality.candidate import ObservationCandidate
from okx_signal_system.strategy.trend_breakout import TradeSignal


def _candidate(symbol: str, score: float, *, side: str = "long") -> SignalCandidate:
    signal = TradeSignal(
        ts=pd.Timestamp("2026-01-01T00:00:00Z"),
        inst_id=symbol,
        side=side,
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


def _history_with_closed_flags(symbol: str, returns: list[float], is_closed: list[object]) -> pd.DataFrame:
    frame = _history(symbol, returns)
    frame["is_closed"] = is_closed
    return frame


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


def test_assign_tiers_does_not_merge_opposite_side_high_correlation_candidates() -> None:
    shared_returns = [0.01, 0.02, -0.01, 0.015, -0.005, 0.02, -0.015, 0.01]
    selection = assign_tiers(
        [
            _candidate("BTC-USDT-SWAP", 9.0, side="long"),
            _candidate("ETH-USDT-SWAP", 8.8, side="short"),
        ],
        max_tier_a=2,
        price_history={
            "BTC-USDT-SWAP": _history("BTC-USDT-SWAP", shared_returns),
            "ETH-USDT-SWAP": _history("ETH-USDT-SWAP", shared_returns),
        },
    )

    assert [item.tier for item in selection.ranked] == ["A", "A"]
    assert selection.ranked[0].correlation_group != selection.ranked[1].correlation_group
    assert len(selection.tier_a) == 2


def test_assign_tiers_marks_insufficient_correlation_samples_as_c_observation() -> None:
    short_returns = [0.01, 0.02, -0.01]
    selection = assign_tiers(
        [
            _candidate("BTC-USDT-SWAP", 9.0),
            _candidate("ETH-USDT-SWAP", 8.8),
        ],
        max_tier_a=2,
        price_history={
            "BTC-USDT-SWAP": _history("BTC-USDT-SWAP", short_returns),
            "ETH-USDT-SWAP": _history("ETH-USDT-SWAP", short_returns),
        },
        min_correlation_samples=8,
    )

    assert [item.tier for item in selection.ranked] == ["A", "A"]
    assert all(item.correlation_group.startswith("unknown:") for item in selection.ranked)
    assert len(selection.tier_a) == 2
    assert len(selection.tier_c) == 0
    assert len(selection.ranked) == 2


def test_assign_tiers_treats_string_false_is_closed_as_unclosed_for_correlation() -> None:
    returns = [0.01, 0.02, -0.01, 0.015, -0.005, 0.02, -0.015, 0.01]
    is_closed = [True, True, True, "false", "0", "no", "", True, True]
    selection = assign_tiers(
        [
            _candidate("BTC-USDT-SWAP", 9.0),
            _candidate("ETH-USDT-SWAP", 8.8),
        ],
        max_tier_a=2,
        price_history={
            "BTC-USDT-SWAP": _history_with_closed_flags("BTC-USDT-SWAP", returns, is_closed),
            "ETH-USDT-SWAP": _history_with_closed_flags("ETH-USDT-SWAP", returns, is_closed),
        },
        min_correlation_samples=5,
    )

    assert [item.tier for item in selection.ranked] == ["A", "A"]
    assert all(item.correlation_group.startswith("unknown:") for item in selection.ranked)


def test_assign_tiers_places_close_non_a_candidates_in_c_observation() -> None:
    selection = assign_tiers(
        [
            _candidate("BTC-USDT-SWAP", 9.0),
            _candidate("ETH-USDT-SWAP", 8.7),
            _candidate("SOL-USDT-SWAP", 7.9),
        ],
        max_tier_a=1,
    )

    assert [item.inst_id for item in selection.ranked] == [
        "BTC-USDT-SWAP",
        "ETH-USDT-SWAP",
        "SOL-USDT-SWAP",
    ]
    assert [item.tier for item in selection.ranked] == ["A", "B", "B"]
    assert selection.tier_c == []


def test_assign_tiers_keeps_non_formal_observation_in_c() -> None:
    formal = _candidate("BTC-USDT-SWAP", 9.0)
    observation = ObservationCandidate(
        inst_id="ETH-USDT-SWAP",
        side="long",
        candle_time=pd.Timestamp("2026-01-01T00:00:00Z"),
        close=100.0,
        breakout_level=100.4,
        breakout_gap_pct=0.004,
        payload={"observation": {"type": "near_breakout"}},
        health_item={"symbol": "ETH-USDT-SWAP", "would_push": False, "observation": True},
        rank_score=8.7,
        raw_score=8.7,
    )

    selection = assign_tiers([formal], observation_candidates=[observation], max_tier_a=1)

    assert [item.tier for item in selection.ranked] == ["A", "C"]
    assert selection.tier_c == [selection.ranked[1]]


def test_assign_tiers_drops_non_push_formal_candidates_from_ranked_tiers() -> None:
    formal = _candidate("BTC-USDT-SWAP", 9.0)
    non_push = replace(
        _candidate("ETH-USDT-SWAP", 8.7),
        health_item={"symbol": "ETH-USDT-SWAP", "would_push": False},
    )

    selection = assign_tiers([formal, non_push], max_tier_a=1)

    assert [item.inst_id for item in selection.ranked] == ["BTC-USDT-SWAP"]
    assert selection.tier_c == []
