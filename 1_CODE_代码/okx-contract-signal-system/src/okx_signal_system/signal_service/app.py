from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import streamlit as st

from okx_signal_system.config import project_paths


def main() -> None:
    st.set_page_config(page_title="OKX Signal System", layout="wide")
    paths = project_paths()
    st.title("OKX Signal System")
    st.caption("本地研究与人工确认；默认不自动实盘下单。")

    summary_path = paths.output_dir / "sample_summary.json"
    trades_path = paths.output_dir / "sample_trades.csv"
    signal_path = paths.output_dir / "latest_signal.json"

    cols = st.columns(3)
    if summary_path.exists():
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        cols[0].metric("Trades", summary.get("total_trades", 0))
        cols[1].metric("Win Rate", round(summary.get("win_rate", 0), 4))
        cols[2].metric("Total Return", round(summary.get("total_return", 0), 4))

    if signal_path.exists():
        st.subheader("Latest Signal")
        st.json(json.loads(signal_path.read_text(encoding="utf-8")))

    if trades_path.exists():
        st.subheader("Trades")
        st.dataframe(pd.read_csv(trades_path), use_container_width=True)


if __name__ == "__main__":
    main()
