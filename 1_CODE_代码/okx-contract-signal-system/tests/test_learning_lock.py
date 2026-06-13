import asyncio
from types import SimpleNamespace

from okx_signal_system.ml.trading_brain import TradingBrain
from okx_signal_system.strategy.trend_breakout import StrategyParams


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


def test_trading_brain_learning_lock_keeps_validated_live_params(tmp_path) -> None:
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
