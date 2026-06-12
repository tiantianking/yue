"""
OKX 合约信号系统 - ML模块
包含：在线学习、强化学习、币种轮换
"""
from okx_signal_system.ml.online_learning import (
    OnlineLearningEngine,
    TradeRecord,
    PerformanceMetrics,
    AdaptationResult,
    create_learning_engine,
)
from okx_signal_system.ml.reinforcement_learning import (
    RLParameterOptimizer,
    MarketRegimeDetector,
    create_rl_optimizer,
)
from okx_signal_system.ml.symbol_rotation import (
    SymbolRotator,
    SymbolPerformance,
    SymbolRotationConfig,
    RotationDecision,
    create_rotator,
)
from okx_signal_system.ml.trading_brain import (
    TradingBrain,
    run_trading_brain,
)

__all__ = [
    "OnlineLearningEngine",
    "TradeRecord",
    "PerformanceMetrics",
    "AdaptationResult",
    "create_learning_engine",
    "RLParameterOptimizer",
    "MarketRegimeDetector",
    "create_rl_optimizer",
    "SymbolRotator",
    "SymbolPerformance",
    "SymbolRotationConfig",
    "RotationDecision",
    "create_rotator",
    "TradingBrain",
    "run_trading_brain",
]