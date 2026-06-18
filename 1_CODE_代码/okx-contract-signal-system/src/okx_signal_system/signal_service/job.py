from __future__ import annotations

import asyncio
import json
from pathlib import Path

from okx_signal_system.data.loader import load_symbol_file
from okx_signal_system.config import load_runtime_config
from okx_signal_system.paths import find_lightweight_history
from okx_signal_system.risk.model import Ledger, RiskConfig
from okx_signal_system.signal_service import SignalScanContext, SignalScanService
from okx_signal_system.signal_service.regime import AdaptiveParamsManager
from okx_signal_system.signal_service.runtime import load_selected_strategy_params_status
from okx_signal_system.strategy.trend_breakout import StrategyParams
from okx_signal_system.timeframe import timeframe_spec


DEFAULT_DATASET = "okx_15m_extended"
DEFAULT_SIGNAL_TIMEFRAME = "15m"
DEFAULT_TREND_TIMEFRAME = "1h"
DEFAULT_SYMBOL_FILE = "BTC_USDT_USDT_15m.parquet"


def latest_signal_payload(
    *,
    dataset: str,
    symbol_file: str,
    inst_id: str,
    signal_timeframe: str = DEFAULT_SIGNAL_TIMEFRAME,
    trend_timeframe: str = DEFAULT_TREND_TIMEFRAME,
    params: StrategyParams | None = None,
) -> dict:
    signal_timeframe = timeframe_spec(signal_timeframe).key
    trend_timeframe = timeframe_spec(trend_timeframe).key
    manifest_status = load_selected_strategy_params_status()
    params = params or manifest_status.params

    async def candle_loader(_inst_id: str, limit: int):
        data = load_symbol_file(find_lightweight_history(dataset) / symbol_file)
        return data.frame.tail(limit).reset_index(drop=True)

    service = SignalScanService(
        candle_loader=candle_loader,
        regime_manager=AdaptiveParamsManager(),
    )
    context = SignalScanContext(
        dataset=dataset,
        signal_timeframe=signal_timeframe,
        trend_timeframe=trend_timeframe,
        strategy_params=params,
        risk_config=load_runtime_config().risk_config(),
        ledger=Ledger(inst_id, init_capital=10000, equity=10000),
        quality_gate_allows_push=bool(manifest_status.ok),
        min_vote_approval_rate=0.4,
        mode="signal_only",
        min_history_bars=50,
        send_health_report=True,
    )
    result = asyncio.run(service.scan_cycle([inst_id], context))
    if result.ready_candidates:
        return result.ready_candidates[0].payload
    if result.cycle_health:
        return {
            "signal": {"inst_id": inst_id, "side": None, "entry_ref": None, "stop_loss": None, "take_profit": None},
            "risk": {"accepted": False, "reason": result.cycle_health[0].get("reason")},
            "live_order_enabled": False,
            "mode": "signal_only",
            "dataset": dataset,
            "signal_timeframe": signal_timeframe,
            "trend_timeframe": trend_timeframe,
            "quality_gate_allows_push": bool(manifest_status.ok),
            "manifest_status": manifest_status.as_dict(),
            "selected_params": {
                "fast_ema": params.fast_ema,
                "slow_ema": params.slow_ema,
                "breakout_window": params.breakout_window,
                "atr_stop_mult": params.atr_stop_mult,
                "take_profit_mult": params.take_profit_mult,
                "max_hold_bars": params.max_hold_bars,
                "atr_window": params.atr_window,
            },
            "cycle_health": result.cycle_health,
        }
    raise RuntimeError("no usable signal row")


def write_latest_signal(
    output_path: str | Path,
    *,
    dataset: str = DEFAULT_DATASET,
    symbol_file: str = DEFAULT_SYMBOL_FILE,
    inst_id: str = "BTC-USDT-SWAP",
    signal_timeframe: str = DEFAULT_SIGNAL_TIMEFRAME,
    trend_timeframe: str = DEFAULT_TREND_TIMEFRAME,
) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = latest_signal_payload(
        dataset=dataset,
        symbol_file=symbol_file,
        inst_id=inst_id,
        signal_timeframe=signal_timeframe,
        trend_timeframe=trend_timeframe,
    )
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return path
