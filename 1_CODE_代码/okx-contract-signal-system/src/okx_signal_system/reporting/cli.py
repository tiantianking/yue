from __future__ import annotations

import argparse

import pandas as pd

from okx_signal_system.config import project_paths
from okx_signal_system.reporting.report_builder import write_report, write_summary_json
from okx_signal_system.signal_service.job import write_latest_signal


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trades", default="")
    args = parser.parse_args()
    paths = project_paths()
    trades_path = args.trades or str(paths.output_dir / "sample_trades.csv")
    trades = pd.read_csv(trades_path)
    write_report(trades, paths.output_dir / "sample_report.md")
    write_summary_json(trades, paths.output_dir / "sample_summary.json")
    write_latest_signal(paths.output_dir / "latest_signal.json")


if __name__ == "__main__":
    main()
