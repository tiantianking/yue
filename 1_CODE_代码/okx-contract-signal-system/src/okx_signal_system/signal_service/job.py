from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from okx_signal_system.data.loader import load_symbol_file
from okx_signal_system.features.indicators import build_feature_frame
from okx_signal_system.paths import find_lightweight_history
from okx_signal_system.risk.model import Ledger, validate_signal
from okx_signal_system.strategy.trend_breakout import StrategyParams, build_signal
from okx_signal_system.timeframe import timeframe_spec
from okx_signal_system.training.startup_quality import load_selected_strategy_params


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
    params = params or load_selected_strategy_params()
    data = load_symbol_file(find_lightweight_history(dataset) / symbol_file)
    features = build_feature_frame(
        data.frame,
        fast_ema=params.fast_ema,
        slow_ema=params.slow_ema,
        breakout_window=params.breakout_window,
        atr_window=params.atr_window,
        signal_timeframe=signal_timeframe,
        trend_timeframe=trend_timeframe,
    ).reset_index(drop=True)
    for idx, row in features.dropna(subset=["atr", "breakout_high", "breakout_low"]).iloc[::-1].iterrows():
        signal = build_signal(row, inst_id=inst_id, params=params, frame=features, idx=int(idx))
        decision = validate_signal(signal, Ledger(inst_id, init_capital=10000, equity=10000))
        payload = {
            "signal": asdict(signal),
            "risk": asdict(decision),
            "live_order_enabled": False,
            "mode": "signal_only",
            "dataset": dataset,
            "signal_timeframe": signal_timeframe,
            "trend_timeframe": trend_timeframe,
            "selected_params": asdict(params),
        }
        return payload
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
