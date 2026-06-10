from __future__ import annotations

from dataclasses import asdict
from itertools import product

import pandas as pd

from okx_signal_system.backtest.runner import run_backtest, summarize_trades
from okx_signal_system.strategy.trend_breakout import StrategyParams


def parameter_grid() -> list[StrategyParams]:
    return [
        StrategyParams(
            fast_ema=fast,
            slow_ema=slow,
            breakout_window=breakout,
            atr_stop_mult=atr_mult,
            take_profit_mult=tp_mult,
            max_hold_bars=max_hold,
        )
        for fast, slow, atr_mult, tp_mult, max_hold in product(
            [10, 20, 30],
            [50, 60, 80],
            [1.5, 2.0, 2.5, 3.0],
            [1.5, 2.0, 3.0, 4.0],
            [24, 48, 72],
        )
        for breakout in [40]
    ]


def run_grid_search(frame: pd.DataFrame, *, inst_id: str, params_grid: list[StrategyParams] | None = None) -> pd.DataFrame:
    rows = []
    for params in params_grid or parameter_grid():
        trades = run_backtest(frame, inst_id=inst_id, params=params)
        summary = summarize_trades(trades)
        rows.append({**asdict(params), **{f"train_{key}": value for key, value in summary.items()}})
    return pd.DataFrame(rows)


def select_best_params(grid_results: pd.DataFrame) -> StrategyParams:
    if grid_results.empty:
        raise ValueError("grid results are empty")
    ranked = grid_results.copy()
    ranked["rank_pf"] = ranked["train_profit_factor"].replace(float("inf"), 999999)
    ranked = ranked.sort_values(["train_status", "rank_pf", "train_total_return", "train_total_trades"], ascending=[False, False, False, False])
    row = ranked.iloc[0]
    return StrategyParams(
        fast_ema=int(row["fast_ema"]),
        slow_ema=int(row["slow_ema"]),
        breakout_window=int(row["breakout_window"]),
        atr_stop_mult=float(row["atr_stop_mult"]),
        take_profit_mult=float(row["take_profit_mult"]),
        max_hold_bars=int(row["max_hold_bars"]),
        atr_window=int(row.get("atr_window", 14)),
    )
