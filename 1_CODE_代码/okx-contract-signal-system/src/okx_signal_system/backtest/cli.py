from __future__ import annotations

import argparse
import json

import pandas as pd

from okx_signal_system.backtest.runner import run_backtest, summarize_trades, validate_backtest_result
from okx_signal_system.config import project_paths
from okx_signal_system.data.loader import load_symbol_file
from okx_signal_system.paths import find_lightweight_history


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default="okx_15m_extended")
    parser.add_argument("--symbol-file", default="BTC_USDT_USDT_15m.parquet")
    parser.add_argument("--inst-id", default="BTC-USDT-SWAP")
    parser.add_argument("--signal-timeframe", default="15m")
    parser.add_argument("--trend-timeframe", default="1h")
    args = parser.parse_args()

    root = find_lightweight_history(args.dataset)
    data = load_symbol_file(root / args.symbol_file)
    trades = validate_backtest_result(
        run_backtest(
            data.frame,
            inst_id=args.inst_id,
            signal_timeframe=args.signal_timeframe,
            trend_timeframe=args.trend_timeframe,
        ),
        context="backtest_cli",
    )
    paths = project_paths()
    paths.output_dir.mkdir(parents=True, exist_ok=True)
    trades_path = paths.output_dir / "sample_trades.csv"
    summary_path = paths.output_dir / "sample_summary.json"
    portfolio_path = paths.output_dir / "portfolio_result.csv"
    trades.to_csv(trades_path, index=False, encoding="utf-8")
    summary_path.write_text(json.dumps(summarize_trades(trades), indent=2), encoding="utf-8")
    pd.DataFrame([{**{"portfolio_name": "sample_single_symbol", "symbols_included": 1}, **summarize_trades(trades)}]).to_csv(
        portfolio_path, index=False, encoding="utf-8"
    )


if __name__ == "__main__":
    main()
