import pandas as pd

from okx_signal_system.ml.shadow_trading import ShadowTradingLedger
from okx_signal_system.risk.model import RiskDecision
from okx_signal_system.strategy.trend_breakout import TradeSignal


def test_shadow_ledger_records_and_scores_take_profit(tmp_path) -> None:
    ledger = ShadowTradingLedger(tmp_path / "shadow.json")
    signal = TradeSignal(
        ts=pd.Timestamp("2026-01-01T00:00:00Z"),
        inst_id="BTC-USDT-SWAP",
        side="long",
        entry_ref=100.0,
        stop_loss=95.0,
        take_profit=130.0,
        max_hold_bars=10,
        reason_codes=("15M_PULLBACK_RECLAIM_UP",),
        signal_score=8.2,
        risk_reward_ratio=6.0,
    )
    decision = RiskDecision(
        accepted=True,
        reason=None,
        leverage_cap=3.0,
        qty=1.0,
        risk_amount=5.0,
        leverage_used=2.0,
        signal_score=8.2,
        risk_reward_ratio=6.0,
    )

    assert ledger.record_signal(signal, decision)
    assert not ledger.record_signal(signal, decision)

    frame = pd.DataFrame(
        [
            {
                "ts": pd.Timestamp("2026-01-01T00:15:00Z"),
                "open": 101.0,
                "high": 131.0,
                "low": 100.0,
                "close": 130.0,
            }
        ]
    )

    assert ledger.update_symbol("BTC-USDT-SWAP", frame) == 1
    summary = ledger.summary()
    assert summary["closed"] == 1
    assert summary["take_profit"] == 1
    assert summary["avg_quality_score"] == 100.0
    assert ledger.score_adjustment("BTC-USDT-SWAP", "long", min_closed=1) == 0.7


def test_shadow_ledger_penalizes_weak_closed_history(tmp_path) -> None:
    ledger = ShadowTradingLedger(tmp_path / "shadow.json")
    for idx in range(2):
        signal = TradeSignal(
            ts=pd.Timestamp("2026-01-01T00:00:00Z") + pd.Timedelta(hours=idx),
            inst_id="ETH-USDT-SWAP",
            side="short",
            entry_ref=100.0,
            stop_loss=105.0,
            take_profit=70.0,
            max_hold_bars=10,
            reason_codes=("15M_PULLBACK_RECLAIM_DOWN",),
            signal_score=7.0,
            risk_reward_ratio=6.0,
        )
        decision = RiskDecision(
            accepted=True,
            reason=None,
            leverage_cap=2.0,
            qty=1.0,
            risk_amount=5.0,
            leverage_used=2.0,
            signal_score=7.0,
            risk_reward_ratio=6.0,
        )
        assert ledger.record_signal(signal, decision)

    frame = pd.DataFrame(
        [
            {
                "ts": pd.Timestamp("2026-01-01T02:15:00Z"),
                "open": 101.0,
                "high": 106.0,
                "low": 100.0,
                "close": 105.0,
            }
        ]
    )

    assert ledger.update_symbol("ETH-USDT-SWAP", frame) == 2
    assert ledger.score_adjustment("ETH-USDT-SWAP", "short", min_closed=2) == -1.0
