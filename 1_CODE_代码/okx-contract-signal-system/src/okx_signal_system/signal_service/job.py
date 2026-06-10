from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from okx_signal_system.data.loader import load_symbol_file
from okx_signal_system.features.indicators import build_feature_frame
from okx_signal_system.paths import find_lightweight_history
from okx_signal_system.risk.model import Ledger, validate_signal
from okx_signal_system.strategy.trend_breakout import build_signal


def latest_signal_payload(*, dataset: str, symbol_file: str, inst_id: str) -> dict:
    data = load_symbol_file(find_lightweight_history(dataset) / symbol_file)
    features = build_feature_frame(data.frame)
    for _, row in features.dropna(subset=["atr", "breakout_high", "breakout_low"]).iloc[::-1].iterrows():
        signal = build_signal(row, inst_id=inst_id)
        decision = validate_signal(signal, Ledger(inst_id, init_capital=10000, equity=10000))
        payload = {
            "signal": asdict(signal),
            "risk": asdict(decision),
            "live_order_enabled": False,
            "mode": "manual_confirmation_only",
        }
        return payload
    raise RuntimeError("no usable signal row")


def write_latest_signal(output_path: str | Path, *, dataset: str = "okx_1h_extended", symbol_file: str = "BTC_USDT_USDT_1h.parquet", inst_id: str = "BTC-USDT-SWAP") -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = latest_signal_payload(dataset=dataset, symbol_file=symbol_file, inst_id=inst_id)
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return path
