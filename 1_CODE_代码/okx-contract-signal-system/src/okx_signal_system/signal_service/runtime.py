from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from okx_signal_system.config import project_paths
from okx_signal_system.strategy.trend_breakout import StrategyParams


def params_from_dict(data: dict) -> StrategyParams:
    return StrategyParams(
        fast_ema=int(data.get("fast_ema", 120)),
        slow_ema=int(data.get("slow_ema", 720)),
        breakout_window=int(data.get("breakout_window", 384)),
        atr_stop_mult=float(data.get("atr_stop_mult", 4.0)),
        take_profit_mult=max(float(data.get("take_profit_mult", 6.0)), 3.5),
        max_hold_bars=int(data.get("max_hold_bars", 768)),
        atr_window=int(data.get("atr_window", 14)),
    )


def load_selected_strategy_params(output_dir: str | Path | None = None) -> StrategyParams:
    out = Path(output_dir) if output_dir else project_paths().output_dir
    path = out / "selected_params.json"
    if not path.exists():
        return StrategyParams()
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return StrategyParams()
    return params_from_dict(data)


def latest_bar_age_hours(frame: pd.DataFrame, now: pd.Timestamp | None = None) -> float | None:
    if frame.empty or "ts" not in frame.columns:
        return None
    latest = pd.to_datetime(frame["ts"].iloc[-1], utc=True)
    ref = now or pd.Timestamp.now(tz="UTC")
    return float((ref - latest).total_seconds() / 3600)


def is_latest_bar_fresh(
    frame: pd.DataFrame,
    *,
    max_lag_hours: float = 3.0,
    now: pd.Timestamp | None = None,
) -> bool:
    age = latest_bar_age_hours(frame, now)
    return age is not None and age <= max_lag_hours


__all__ = [
    "is_latest_bar_fresh",
    "latest_bar_age_hours",
    "load_selected_strategy_params",
    "params_from_dict",
]
