from __future__ import annotations

from dataclasses import asdict

import pandas as pd

from okx_signal_system.backtest.evaluation import evaluate_symbol
from okx_signal_system.backtest.grid_search import run_grid_search, select_best_params
from okx_signal_system.backtest.runner import run_backtest, split_train_valid, summarize_trades
from okx_signal_system.data.loader import load_all_symbols
from okx_signal_system.strategy.trend_breakout import StrategyParams


def run_train_valid_symbol(
    frame: pd.DataFrame,
    *,
    inst_id: str,
    params_grid: list[StrategyParams] | None = None,
) -> dict:
    train_frame, valid_frame = split_train_valid(frame, valid_fraction=0.25)
    grid = run_grid_search(train_frame, inst_id=inst_id, params_grid=params_grid)
    selected = select_best_params(grid)
    train_trades = run_backtest(train_frame, inst_id=inst_id, params=selected)
    valid_trades = run_backtest(valid_frame, inst_id=inst_id, params=selected)
    train_summary = summarize_trades(train_trades)
    valid_summary = summarize_trades(valid_trades)
    evaluation = evaluate_symbol(train_summary, valid_summary)
    return {
        "inst_id": inst_id,
        "selected_params": asdict(selected),
        "grid_results": grid,
        "train_trades": train_trades,
        "valid_trades": valid_trades,
        "train_summary": train_summary,
        "valid_summary": valid_summary,
        "evaluation": evaluation,
    }


def run_dataset_research(
    *,
    dataset: str = "okx_1h_extended",
    params_grid: list[StrategyParams] | None = None,
    max_symbols: int | None = None,
) -> pd.DataFrame:
    rows = []
    symbols = load_all_symbols(dataset)
    if max_symbols is not None:
        symbols = symbols[:max_symbols]
    for symbol_data in symbols:
        result = run_train_valid_symbol(symbol_data.frame, inst_id=symbol_data.inst_id, params_grid=params_grid)
        rows.append(
            {
                "symbol": symbol_data.inst_id,
                **{f"train_{key}": value for key, value in result["train_summary"].items()},
                **{f"valid_{key}": value for key, value in result["valid_summary"].items()},
                **result["selected_params"],
                "pass_fail": result["evaluation"]["pass_fail"],
                "fail_reasons": result["evaluation"]["reasons"],
            }
        )
    return pd.DataFrame(rows)
