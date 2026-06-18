from __future__ import annotations

from dataclasses import replace

import pandas as pd
import pytest

from okx_signal_system.risk.model import RiskDecision
from okx_signal_system.signal_quality import SignalCandidate, assign_tiers
from okx_signal_system.signal_quality.selector import absolute_quality_score
from okx_signal_system.signal_quality.candidate import ObservationCandidate
from okx_signal_system.signal_quality.observation import (
    breakout_distance_atr,
    near_breakout_observation,
)
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


def test_assign_tiers_requires_absolute_quality_for_a_tier() -> None:
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
    assert [item.tier for item in selection.ranked] == ["A", "B", "B"]
    assert [item.rank for item in selection.ranked] == [1, 2, 3]
    assert selection.ranked[0].health_item["quality_score"] >= 80
    assert selection.ranked[1].health_item["quality_score"] < 80
    assert len(selection.tier_a) == 1
    assert len(selection.tier_b) == 2


def test_assign_tiers_single_barely_passing_candidate_is_not_a() -> None:
    candidate = _candidate("BTC-USDT-SWAP", 6.2)

    selection = assign_tiers([candidate], max_tier_a=2)

    assert selection.ranked[0].tier == "B"
    assert selection.ranked[0].health_item["tier_reason"] == "below_a_quality_threshold"
    assert absolute_quality_score(candidate) < 80


def test_assign_tiers_demotes_below_b_quality_to_c_observation() -> None:
    candidate = _candidate("BTC-USDT-SWAP", 4.0)

    selection = assign_tiers([candidate], max_tier_a=2)

    assert selection.tier_a == []
    assert selection.tier_b == []
    assert selection.ranked[0].tier == "C"
    assert selection.ranked[0].health_item["tier_reason"] == "below_b_quality_threshold"


def test_assign_tiers_allows_three_unrelated_absolute_a_candidates() -> None:
    selection = assign_tiers(
        [
            _candidate("BTC-USDT-SWAP", 9.2),
            _candidate("ETH-USDT-SWAP", 8.8),
            _candidate("SOL-USDT-SWAP", 8.5),
        ],
        max_a_per_cycle=4,
        min_correlation_samples=8,
    )

    assert [item.tier for item in selection.ranked] == ["A", "A", "A"]
    assert all(item.health_item["quality_score"] >= 80 for item in selection.tier_a)
    assert all(item.payload["quality_score"] == item.health_item["quality_score"] for item in selection.tier_a)


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
        min_correlation_samples=8,
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
        min_correlation_samples=8,
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
        min_correlation_samples=8,
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
        min_correlation_samples=8,
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


def test_assign_tiers_c_high_score_does_not_affect_formal_rank() -> None:
    low_formal = _candidate("BTC-USDT-SWAP", 6.8)
    high_formal = _candidate("ETH-USDT-SWAP", 8.1)
    observation = ObservationCandidate(
        inst_id="SOL-USDT-SWAP",
        side="long",
        candle_time=pd.Timestamp("2026-01-01T00:00:00Z"),
        close=100.0,
        breakout_level=100.4,
        breakout_gap_pct=0.004,
        payload={"observation": {"type": "near_breakout"}},
        health_item={"symbol": "SOL-USDT-SWAP", "would_push": False, "observation": True},
        rank_score=99.0,
        raw_score=99.0,
    )

    selection = assign_tiers(
        [low_formal, high_formal],
        observation_candidates=[observation],
        max_tier_a=1,
    )

    assert [item.inst_id for item in selection.ranked] == [
        "ETH-USDT-SWAP",
        "BTC-USDT-SWAP",
        "SOL-USDT-SWAP",
    ]
    assert [item.tier for item in selection.ranked] == ["A", "B", "C"]
    assert [item.rank for item in selection.tier_a + selection.tier_b] == [1, 2]
    assert selection.tier_c[0].rank is None
    assert selection.tier_c[0].watch_rank == 1


def test_assign_tiers_drops_non_push_formal_candidates_from_ranked_tiers() -> None:
    formal = _candidate("BTC-USDT-SWAP", 9.0)
    non_push = replace(
        _candidate("ETH-USDT-SWAP", 8.7),
        health_item={"symbol": "ETH-USDT-SWAP", "would_push": False},
    )

    selection = assign_tiers([formal, non_push], max_tier_a=1)

    assert [item.inst_id for item in selection.ranked] == ["BTC-USDT-SWAP"]
    assert selection.tier_c == []


def test_near_breakout_observation_uses_atr_distance_across_price_levels() -> None:
    long_low = pd.Series(
        {"close": 100.0, "atr": 10.0, "breakout_high": 102.5, "trend_bias": "long"}
    )
    long_high = pd.Series(
        {"close": 10000.0, "atr": 1000.0, "breakout_high": 10250.0, "trend_bias": "long"}
    )

    low_observation = near_breakout_observation(long_low)
    high_observation = near_breakout_observation(long_high)

    assert low_observation is not None
    assert high_observation is not None
    assert low_observation[4] == high_observation[4] == pytest.approx(0.25)
    assert breakout_distance_atr(long_low) == pytest.approx(0.25)
    assert breakout_distance_atr(long_high) == pytest.approx(0.25)
    assert low_observation[3] == pytest.approx(0.025)
    assert high_observation[3] == pytest.approx(0.025)


def test_near_breakout_observation_rejects_distance_above_atr_threshold() -> None:
    row = pd.Series({"close": 100.0, "atr": 10.0, "breakout_high": 103.2, "trend_bias": "long"})

    assert breakout_distance_atr(row) == pytest.approx(0.32)
    assert near_breakout_observation(row) is None
