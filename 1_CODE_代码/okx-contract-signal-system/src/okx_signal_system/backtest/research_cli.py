from __future__ import annotations

import argparse

from okx_signal_system.backtest.research import run_dataset_research
from okx_signal_system.config import project_paths
from okx_signal_system.strategy.trend_breakout import StrategyParams


def smoke_grid() -> list[StrategyParams]:
    return [
        StrategyParams(fast_ema=10, slow_ema=50, breakout_window=20, atr_stop_mult=1.5, take_profit_mult=1.5, max_hold_bars=24),
        StrategyParams(fast_ema=20, slow_ema=60, breakout_window=40, atr_stop_mult=2.0, take_profit_mult=2.0, max_hold_bars=48),
        StrategyParams(fast_ema=30, slow_ema=80, breakout_window=60, atr_stop_mult=3.0, take_profit_mult=4.0, max_hold_bars=72),
    ]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default="okx_1h_extended")
    parser.add_argument("--max-symbols", type=int, default=3)
    parser.add_argument("--full-grid", action="store_true")
    args = parser.parse_args()
    params_grid = None if args.full_grid else smoke_grid()
    results = run_dataset_research(dataset=args.dataset, params_grid=params_grid, max_symbols=args.max_symbols)
    output = project_paths().output_dir / "symbol_train_valid_results.csv"
    output.parent.mkdir(parents=True, exist_ok=True)
    results.to_csv(output, index=False, encoding="utf-8")


if __name__ == "__main__":
    main()
