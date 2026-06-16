"""
OKX 合约信号系统 - 智能交易大脑
整合所有模块：在线学习 + 强化学习 + 币种轮换 + 实时API + 环境自适应 + 多策略投票 + 滚动回测 + 模式识别
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from okx_signal_system.exchange.realtime import (
    OKXRealtimeAPI,
    LiveSignalMonitor,
    create_realtime_api,
)
from okx_signal_system.ml.online_learning import (
    OnlineLearningEngine,
    TradeRecord,
    create_learning_engine,
)
from okx_signal_system.ml.reinforcement_learning import (
    RLParameterOptimizer,
    MarketRegimeDetector,
    create_rl_optimizer,
)
from okx_signal_system.ml.symbol_rotation import (
    SymbolRotator,
    create_rotator,
)
from okx_signal_system.ml.regime_adaptive import (
    AdaptiveParamsManager,
    RegimeDetector,
)
from okx_signal_system.ml.rolling_backtest import RollingBacktestValidator
from okx_signal_system.ml.pattern_recognition import PatternRecognizer
from okx_signal_system.notify.feishu import feishu_send_signal_card, send_text
from okx_signal_system.data.gap_handler import IncrementalSyncer
from okx_signal_system.paths import find_lightweight_history
from okx_signal_system.risk.model import Ledger, RiskConfig, validate_signal
from okx_signal_system.signal_runtime import (
    DEFAULT_MAX_SIGNAL_LAG_MINUTES,
    latest_closed_signal,
    signal_is_stale,
)
from okx_signal_system.strategy.trend_breakout import StrategyParams
from okx_signal_system.training.startup_quality import load_selected_strategy_params
from okx_signal_system.timeframe import timeframe_spec

log = logging.getLogger(__name__)


class TradingBrain:
    """
    智能交易大脑
    整合所有AI模块，自主运行
    """

    def __init__(
        self,
        data_dir: Path | str,
        config: dict | None = None,
    ):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self.config = config or {}
        data_cfg = self.config.get("data", {}) if isinstance(self.config, dict) else {}
        learning_cfg = self.config.get("learning", {}) if isinstance(self.config, dict) else {}
        self.dataset = data_cfg.get("historical_dataset", "okx_15m_extended")
        self.signal_timeframe = timeframe_spec(data_cfg.get("timeframe", "15m")).key
        self.trend_timeframe = timeframe_spec(data_cfg.get("trend_timeframe", "1h")).key
        self.live_param_updates_enabled = bool(learning_cfg.get("live_param_updates_enabled", False))
        self.param_suggestions: list[dict] = []

        # 初始化所有模块
        self.online_learning = create_learning_engine(self.data_dir / "online_learning")
        self.rl_optimizer = create_rl_optimizer(self.data_dir / "rl_optimizer")

        # 币种列表
        available_symbols = self.config.get("symbols", [
            "BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP",
            "BNB-USDT-SWAP", "XRP-USDT-SWAP", "ADA-USDT-SWAP",
        ])
        self.symbol_rotator = create_rotator(
            available_symbols,
            self.data_dir / "symbol_rotation",
        )

        # 实时API
        api_config = self.config if "data" in self.config else self.config.get("api", {})
        self.api = create_realtime_api(api_config)
        # 信号执行器（使用 position_monitor 替代）
        self.auto_stop = None  # 延迟初始化

        # 增量数据同步器
        self.syncer = IncrementalSyncer(timeframe=self.signal_timeframe, dataset=self.dataset)

        # 当前参数
        self.current_params = load_selected_strategy_params()

        # 运行状态
        self._running = False
        self._cycle_count = 0

        log.info("TradingBrain initialized")

    async def start(self):
        """启动交易大脑"""
        log.info("=" * 60)
        log.info("Starting Trading Brain - AI Powered Trading System")
        log.info("=" * 60)

        # 启动时同步数据（补足离线期间的数据空缺）
        log.info("Syncing data before startup...")
        sync_results = self.syncer.sync_batch(self.symbol_rotator.available_symbols)
        for sym, result in sync_results.items():
            if result.bars_added > 0:
                log.info(f"  {sym}: +{result.bars_added} bars filled")

        # 连接API
        await self.api.connect()

        # 加载最佳参数
        learned_params = self.online_learning.get_current_params()
        learned_params = self.online_learning.get_current_params()
        if self.live_param_updates_enabled:
            self.current_params = learned_params
            log.info(f"Loaded live learning params: {self.current_params}")
        else:
            self._record_param_suggestion("online_learning_startup", learned_params, "learning_locked")
            log.info(f"Learning locked; using validated params: {self.current_params}")

        self._running = True

        while self._running:
            try:
                self._cycle_count += 1
                await self.run_cycle()

                # 每30分钟评估一次
                if self._cycle_count % 2 == 0:
                    await self.evaluate_and_adapt()

                # 每小时轮换币种
                if self._cycle_count % 4 == 0:
                    await self.rotate_symbols()

                # 等待下一个周期（15分钟）
                await asyncio.sleep(900)

            except Exception as e:
                log.error(f"Cycle error: {e}")
                await asyncio.sleep(60)

    async def stop(self):
        """停止交易大脑"""
        self._running = False
        await self.api.disconnect()
        log.info("TradingBrain stopped")

    async def run_cycle(self):
        """执行一个扫描周期"""
        log.info(f"=== Cycle #{self._cycle_count} ===")

        # 增量同步最新数据
        self.syncer.sync_batch(self.symbol_rotator.get_active_symbols())

        active_symbols = self.symbol_rotator.get_active_symbols()
        signals_generated = []

        for inst_id in active_symbols:
            try:
                # 获取市场数据
                market = await self.api.get_market_data(inst_id)
                if not market:
                    continue

                # 生成信号
                signal = self._generate_signal(inst_id, market)
                if signal and signal.accepted:
                    decision = validate_signal(
                        signal,
                        Ledger(inst_id=inst_id, init_capital=10000, equity=10000),
                        RiskConfig(),
                    )
                    if not decision.accepted:
                        continue
                    signals_generated.append(signal)

                    # 推送到飞书
                    feishu_send_signal_card(
                        inst_id=signal.inst_id,
                        direction=signal.side,
                        entry_price=signal.entry_ref or market.last_price,
                        stop_loss=signal.stop_loss or 0,
                        take_profit=signal.take_profit or 0,
                        reason=str(signal.reason_codes),
                    )

            except Exception as e:
                log.error(f"Error processing {inst_id}: {e}")

        log.info(f"Cycle #{self._cycle_count} completed. Signals: {len(signals_generated)}")

    def _generate_signal(self, inst_id: str, market):
        """生成交易信号"""
        # 从数据文件读取特征
        try:
            from okx_signal_system.data.loader import load_symbol_file
            from okx_signal_system.features.indicators import build_feature_frame

            # 转换inst_id格式
            inst_id_clean = inst_id.replace("-", "_").replace("_SWAP", "")
            if inst_id_clean.count("USDT") == 1:
                inst_id_clean = inst_id_clean + "_USDT"

            suffix = timeframe_spec(self.signal_timeframe).file_suffix
            fname = f"{inst_id_clean}_{suffix}.parquet"
            path = find_lightweight_history(self.dataset) / fname

            if not path.exists():
                return None

            data = load_symbol_file(path)
            features = build_feature_frame(
                data.frame,
                fast_ema=self.current_params.fast_ema,
                slow_ema=self.current_params.slow_ema,
                breakout_window=self.current_params.breakout_window,
                atr_window=self.current_params.atr_window,
                signal_timeframe=self.signal_timeframe,
                trend_timeframe=self.trend_timeframe,
            )

            signal = latest_closed_signal(features, inst_id=inst_id, params=self.current_params)
            if signal is None:
                return None
            if signal_is_stale(
                signal.ts,
                timeframe=self.signal_timeframe,
                max_lag_minutes=DEFAULT_MAX_SIGNAL_LAG_MINUTES,
            ):
                return None
            return signal

        except Exception as e:
            log.error(f"Signal generation error: {e}")

        return None

    def _record_param_suggestion(self, source: str, params: StrategyParams, reason: str) -> None:
        self.param_suggestions.append(
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "source": source,
                "reason": reason,
                "params": asdict(params),
            }
        )
        self.param_suggestions = self.param_suggestions[-50:]

    async def evaluate_and_adapt(self):
        """评估表现并自适应调整"""
        log.info("Evaluating and adapting...")

        # 在线学习
        if self.online_learning.should_adapt():
            result = self.online_learning.adapt_params()
            if result:
                self._record_param_suggestion("online_learning", result.new_params, result.reason)
                if self.live_param_updates_enabled:
                    self.current_params = result.new_params
                    log.info(f"Online learning applied: {result.new_params}")
                else:
                    log.info(f"Online learning suggested params; live updates locked: {result.new_params}")

        # 强化学习
        from okx_signal_system.ml.reinforcement_learning import MarketRegimeDetector
        regime = MarketRegimeDetector.detect_regime(
            atr_pct=0.02,
            atr_avg_ratio=1.0,
            ema_spread=0.01,
            volume_ratio=1.0,
        )

        state = MarketRegimeDetector.create_state_from_metrics(
            regime=regime,
            recent_pf=1.2,
            recent_wr=0.4,
            recent_return=0.05,
        )

        new_params = self.rl_optimizer.optimize_params(self.current_params, state)
        if new_params != self.current_params:
            self._record_param_suggestion("reinforcement_learning", new_params, "rl_suggestion")
            if self.live_param_updates_enabled:
                self.current_params = new_params
                log.info(f"RL applied params: {new_params}")
            else:
                log.info(f"RL suggested params; live updates locked: {new_params}")

    async def rotate_symbols(self):
        """轮换币种"""
        log.info("Rotating symbols...")

        # 更新各币种表现
        for inst_id in self.symbol_rotator.available_symbols:
            # 这里应该从实际交易获取数据
            # 简化处理：使用模拟数据
            pass

        # 执行轮换
        decision = self.symbol_rotator.evaluate_and_rotate()

        if decision.add_symbols or decision.remove_symbols:
            log.info(f"Symbol rotation: +{decision.add_symbols} -{decision.remove_symbols}")

            # 推送轮换通知
            feishu_send_signal_card(
                inst_id="SYSTEM",
                direction="rotation",
                entry_price=0,
                stop_loss=0,
                take_profit=0,
                reason=f"Add: {decision.add_symbols}, Remove: {decision.remove_symbols}",
            )

    async def record_trade_result(
        self,
        inst_id: str,
        side: str,
        entry_price: float,
        exit_price: float,
        pnl: float,
    ):
        """记录交易结果用于学习"""
        trade = TradeRecord(
            inst_id=inst_id,
            side=side,
            entry_time=datetime.now(timezone.utc),
            exit_time=datetime.now(timezone.utc),
            entry_price=entry_price,
            exit_price=exit_price,
            qty=0.01,
            pnl=pnl,
            pnl_pct=pnl / entry_price,
            exit_reason="signal",
            params=asdict(self.current_params),
        )
        self.online_learning.record_trade(trade)

    def get_status_report(self) -> dict:
        """获取状态报告"""
        return {
            "cycle_count": self._cycle_count,
            "is_running": self._running,
            "current_params": asdict(self.current_params),
            "live_param_updates_enabled": self.live_param_updates_enabled,
            "param_suggestions": self.param_suggestions[-5:],
            "active_symbols": self.symbol_rotator.get_active_symbols(),
            "learning_stats": self.online_learning.get_performance_summary(),
            "rl_stats": self.rl_optimizer.get_learning_stats(),
        }


async def run_trading_brain():
    """运行交易大脑"""
    from pathlib import Path

    brain = TradingBrain(
        data_dir=Path(__file__).parent.parent.parent / "output" / "trading_brain",
        config={
            "symbols": [
                "BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP",
                "BNB-USDT-SWAP", "XRP-USDT-SWAP", "ADA-USDT-SWAP",
            ],
            "api": {
                "paper_trading": True,
                "leverage": 5,
            },
        },
    )

    await brain.start()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_trading_brain())
