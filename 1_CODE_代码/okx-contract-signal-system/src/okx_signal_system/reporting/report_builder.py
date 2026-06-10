from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from okx_signal_system.backtest.runner import summarize_trades


def build_markdown_report(trades: pd.DataFrame, *, title: str = "OKX Backtest Report") -> str:
    summary = summarize_trades(trades)
    lines = [
        f"# {title}",
        "",
        f"- total_return: {summary['total_return']}",
        f"- profit_factor: {summary['profit_factor']}",
        f"- win_rate: {summary['win_rate']}",
        f"- total_trades: {summary['total_trades']}",
        f"- status: {summary['status']}",
        "",
        "默认不自动实盘下单；本报告只用于本地研究和人工确认。",
    ]
    return "\n".join(lines)


def write_report(trades: pd.DataFrame, output_path: str | Path) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_markdown_report(trades), encoding="utf-8")
    return path


def write_summary_json(trades: pd.DataFrame, output_path: str | Path) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summarize_trades(trades), indent=2), encoding="utf-8")
    return path
