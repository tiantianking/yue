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
