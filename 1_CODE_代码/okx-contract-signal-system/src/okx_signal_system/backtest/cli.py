from __future__ import annotations

import argparse
import json

from okx_signal_system.backtest.runner import run_backtest, summarize_trades
from okx_signal_system.config import project_paths
from okx_signal_system.data.loader import load_symbol_file
from okx_signal_system.paths import find_lightweight_history


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default="okx_1h_extended")
    parser.add_argument("--symbol-file", default="BTC_USDT_USDT_1h.parquet")
    parser.add_argument("--inst-id", default="BTC-USDT-SWAP")
    args = parser.parse_args()

    root = find_lightweight_history(args.dataset)
    data = load_symbol_file(root / args.symbol_file)
    trades = run_backtest(data.frame, inst_id=args.inst_id)
    paths = project_paths()
    paths.output_dir.mkdir(parents=True, exist_ok=True)
    trades_path = paths.output_dir / "sample_trades.csv"
    summary_path = paths.output_dir / "sample_summary.json"
    trades.to_csv(trades_path, index=False, encoding="utf-8")
    summary_path.write_text(json.dumps(summarize_trades(trades), indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
