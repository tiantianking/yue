"""Experimental signal-only research brain for OKX market data."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from okx_signal_system.exchange.realtime import (
    create_realtime_api,
)
from okx_signal_system.config import load_runtime_config
from okx_signal_system.ml.online_learning import (
    TradeRecord,
    create_learning_engine,
)
from okx_signal_system.ml.reinforcement_learning import (
    create_rl_optimizer,
)
from okx_signal_system.ml.symbol_rotation import (
    create_rotator,
)
from okx_signal_system.ml.regime_adaptive import (
    AdaptiveParamsManager,
)
from okx_signal_system.notify import NotificationDispatcher
from okx_signal_system.data.gap_handler import IncrementalSyncer
from okx_signal_system.risk.model import Ledger
from okx_signal_system.signal_service import SignalScanContext, SignalScanService
from okx_signal_system.strategy.trend_breakout import StrategyParams
from okx_signal_system.strategy.vote_gate import min_vote_approval_rate
from okx_signal_system.training.startup_quality import load_selected_strategy_params
from okx_signal_system.timeframe import timeframe_spec

log = logging.getLogger(__name__)


class TradingBrain:
    """Coordinate research-only learning, rotation, scanning, and notifications."""

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
        self.live_param_updates_enabled = False
        self.live_param_updates_requested = bool(learning_cfg.get("live_param_updates_enabled", False))
        self.param_suggestions: list[dict] = []

        self.online_learning = create_learning_engine(self.data_dir / "online_learning")
        self.rl_optimizer = create_rl_optimizer(self.data_dir / "rl_optimizer")

        available_symbols = self.config.get("symbols", [
            "BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP",
            "BNB-USDT-SWAP", "XRP-USDT-SWAP", "ADA-USDT-SWAP",
        ])
        self.symbol_rotator = create_rotator(
            available_symbols,
            self.data_dir / "symbol_rotation",
        )

        api_config = self.config if "data" in self.config else self.config.get("api", {})
        self.api = create_realtime_api(api_config)
        self.auto_stop = None

        self.syncer: IncrementalSyncer | None = None

        self.current_params = load_selected_strategy_params()

        self._running = False
        self._cycle_count = 0
        self._regime_mgr = AdaptiveParamsManager()
        self._risk_cfg = load_runtime_config().risk_config()
        self._ledger = Ledger("trading_brain", init_capital=10000, equity=10000)
        self._notification_dispatcher = NotificationDispatcher()
        self._scan_service = SignalScanService(
            candle_loader=self._load_signal_candles,
            regime_manager=self._regime_mgr,
        )

        log.info("TradingBrain initialized")

    def _get_syncer(self) -> IncrementalSyncer | None:
        if self.syncer is not None:
            return self.syncer
        try:
            self.syncer = IncrementalSyncer(timeframe=self.signal_timeframe, dataset=self.dataset)
        except FileNotFoundError as exc:
            log.warning("Historical dataset unavailable; skipping TradingBrain history sync: %s", exc)
            return None
        return self.syncer

    async def start(self):
        """Start the experimental trading brain loop."""
        log.info("=" * 60)
        log.info("Starting Trading Brain - AI Powered Trading System")
        log.info("=" * 60)

        log.info("Syncing data before startup...")
        syncer = self._get_syncer()
        if syncer is not None:
            sync_results = syncer.sync_batch(self.symbol_rotator.available_symbols)
            for sym, result in sync_results.items():
                if result.bars_added > 0:
                    log.info(f"  {sym}: +{result.bars_added} bars filled")

        await self.api.connect()

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

                if self._cycle_count % 2 == 0:
                    await self.evaluate_and_adapt()

                if self._cycle_count % 4 == 0:
                    await self.rotate_symbols()

                await asyncio.sleep(900)

            except Exception as e:
                log.error(f"Cycle error: {e}")
                await asyncio.sleep(60)

    async def stop(self):
        """Stop the experimental trading brain."""
        self._running = False
        await self.api.disconnect()
        log.info("TradingBrain stopped")

    async def run_cycle(self):
        """Run one scan cycle."""
        log.info(f"=== Cycle #{self._cycle_count} ===")

        syncer = self._get_syncer()
        if syncer is not None:
            syncer.sync_batch(self.symbol_rotator.get_active_symbols())

        active_symbols = self.symbol_rotator.get_active_symbols()
        try:
            result = await self._scan_service.scan_cycle(
                active_symbols,
                SignalScanContext(
                    dataset=self.dataset,
                    signal_timeframe=self.signal_timeframe,
                    trend_timeframe=self.trend_timeframe,
                    strategy_params=self.current_params,
                    risk_config=self._risk_cfg,
                    ledger=self._ledger,
                    quality_gate_allows_push=False,
                    min_vote_approval_rate=min_vote_approval_rate(self.config),
                    mode="trading_brain_observation_only",
                    min_history_bars=50,
                    send_health_report=True,
                ),
            )
        except Exception as e:
            log.error("TradingBrain scan failed: %s", e)
            return

        log.info(
            "Cycle #%s completed. A=%s B=%s C=%s checked=%s",
            self._cycle_count,
            len(result.selection.tier_a),
            len(result.selection.tier_b),
            len(result.selection.tier_c),
            len(result.cycle_health),
        )

    async def _load_signal_candles(self, inst_id: str, limit: int):
        return await self.api.get_candles(inst_id, bar=self.signal_timeframe, limit=limit)

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
        """Evaluate experimental learning modules."""
        log.info("Evaluating and adapting...")

        if self.online_learning.should_adapt():
            result = self.online_learning.adapt_params()
            if result:
                self._record_param_suggestion("online_learning", result.new_params, result.reason)
                if self.live_param_updates_enabled:
                    self.current_params = result.new_params
                    log.info(f"Online learning applied: {result.new_params}")
                else:
                    log.info(f"Online learning suggested params; live updates locked: {result.new_params}")

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
        """Rotate active research symbols."""
        log.info("Rotating symbols...")

        for inst_id in self.symbol_rotator.available_symbols:
            # Rotation performance can be updated here when research results are available.
            pass

        decision = self.symbol_rotator.evaluate_and_rotate()

        if decision.add_symbols or decision.remove_symbols:
            log.info(f"Symbol rotation: +{decision.add_symbols} -{decision.remove_symbols}")

    async def record_trade_result(
        self,
        inst_id: str,
        side: str,
        entry_price: float,
        exit_price: float,
        pnl: float,
    ):
        """Record one research trade result."""
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
        """Return status."""
        return {
            "cycle_count": self._cycle_count,
            "is_running": self._running,
            "current_params": asdict(self.current_params),
            "live_param_updates_enabled": self.live_param_updates_enabled,
            "live_param_updates_requested": self.live_param_updates_requested,
            "param_suggestions": self.param_suggestions[-5:],
            "active_symbols": self.symbol_rotator.get_active_symbols(),
            "learning_stats": self.online_learning.get_performance_summary(),
            "rl_stats": self.rl_optimizer.get_learning_stats(),
        }


async def run_trading_brain():
    """Run TradingBrain."""
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
