import asyncio
from types import SimpleNamespace

import pandas as pd

from okx_signal_system.ml.regime_adaptive import AdaptiveParamsManager
from okx_signal_system.ml.shadow_trading import ShadowTradingLedger
from okx_signal_system.ml.trading_brain import TradingBrain
from okx_signal_system.risk.model import RiskDecision
from okx_signal_system.strategy.trend_breakout import StrategyParams
from okx_signal_system.strategy.trend_breakout import TradeSignal


class DummyOnlineLearning:
    def should_adapt(self) -> bool:
        return True

    def adapt_params(self):
        return SimpleNamespace(
            new_params=StrategyParams(fast_ema=144),
            reason="test_suggestion",
        )

    def get_performance_summary(self) -> dict:
        return {}


class DummyRLOptimizer:
    def optimize_params(self, current_params: StrategyParams, state):
        return StrategyParams(slow_ema=960)

    def get_learning_stats(self) -> dict:
        return {}


def test_trading_brain_learning_lock_keeps_validated_live_params(tmp_path, monkeypatch) -> None:
    def fail_history_lookup(*_args, **_kwargs):
        raise FileNotFoundError("missing local history")

    monkeypatch.setattr("okx_signal_system.data.gap_handler.find_lightweight_history", fail_history_lookup)

    brain = TradingBrain(
        tmp_path,
        config={
            "symbols": [],
            "data": {
                "historical_dataset": "okx_15m_extended",
                "timeframe": "15m",
                "trend_timeframe": "1h",
            },
            "learning": {"live_param_updates_enabled": False},
        },
    )
    original_params = brain.current_params
    brain.online_learning = DummyOnlineLearning()
    brain.rl_optimizer = DummyRLOptimizer()

    asyncio.run(brain.evaluate_and_adapt())

    assert brain.current_params == original_params
    assert [item["source"] for item in brain.param_suggestions] == [
        "online_learning",
        "reinforcement_learning",
    ]


def test_trading_brain_ignores_live_param_update_request(tmp_path, monkeypatch) -> None:
    def fail_history_lookup(*_args, **_kwargs):
        raise FileNotFoundError("missing local history")

    monkeypatch.setattr("okx_signal_system.data.gap_handler.find_lightweight_history", fail_history_lookup)

    brain = TradingBrain(
        tmp_path,
        config={
            "symbols": [],
            "data": {
                "historical_dataset": "okx_15m_extended",
                "timeframe": "15m",
                "trend_timeframe": "1h",
            },
            "learning": {"live_param_updates_enabled": True},
        },
    )
    original_params = brain.current_params
    brain.online_learning = DummyOnlineLearning()
    brain.rl_optimizer = DummyRLOptimizer()

    asyncio.run(brain.evaluate_and_adapt())

    assert brain.live_param_updates_requested is True
    assert brain.live_param_updates_enabled is False
    assert brain.current_params == original_params
    assert len(brain.param_suggestions) == 2


def test_shadow_scoring_is_offline_only_for_realtime_decision_path(tmp_path, monkeypatch) -> None:
    ledger = ShadowTradingLedger(tmp_path / "shadow.json")
    signal = TradeSignal(
        ts=pd.Timestamp("2026-01-01T00:00:00Z"),
        inst_id="BTC-USDT-SWAP",
        side="long",
        entry_ref=100.0,
        stop_loss=95.0,
        take_profit=130.0,
        max_hold_bars=10,
        reason_codes=("TEST",),
        signal_score=8.2,
        risk_reward_ratio=6.0,
    )
    decision = RiskDecision(
        accepted=True,
        reason=None,
        signal_score=8.2,
        risk_reward_ratio=6.0,
    )
    assert ledger.record_signal(signal, decision)
    assert ledger.update_symbol(
        "BTC-USDT-SWAP",
        pd.DataFrame(
            [
                {
                    "ts": pd.Timestamp("2026-01-01T00:15:00Z"),
                    "open": 101.0,
                    "high": 131.0,
                    "low": 100.0,
                    "close": 130.0,
                }
            ]
        ),
    ) == 1

    assert ledger.offline_score_adjustment("BTC-USDT-SWAP", "long", min_closed=1) == 0.7
    monkeypatch.setattr("okx_signal_system.ml.shadow_trading._called_from_realtime_decision_path", lambda: True)
    assert ledger.score_adjustment("BTC-USDT-SWAP", "long", min_closed=1) == 0.0


def test_regime_adaptive_penalty_is_observation_only() -> None:
    manager = AdaptiveParamsManager()
    manager.current_regime = "low_vol_range"

    assert manager.offline_score_penalty() < 0
    assert manager.offline_leverage_factor() < 1.0
    assert manager.get_score_penalty() == 0.0
    assert manager.get_leverage_factor() == 1.0
    summary = manager.get_regime_summary()
    assert summary["score_penalty"] == 0.0
    assert summary["observed_score_penalty"] < 0
