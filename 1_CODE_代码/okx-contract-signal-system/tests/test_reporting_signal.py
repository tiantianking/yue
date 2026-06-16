import json

import pandas as pd
import pytest

from tests._integration import require_lightweight_history
from okx_signal_system.backtest.runner import empty_backtest_frame, run_backtest
from okx_signal_system.data.loader import load_symbol_file
from okx_signal_system.reporting.report_builder import build_markdown_report
from okx_signal_system.signal_service.job import latest_signal_payload


def test_markdown_report_rejects_empty_or_incomplete_backtest() -> None:
    with pytest.raises(ValueError, match="markdown_report produced no backtest rows"):
        build_markdown_report(empty_backtest_frame())

    with pytest.raises(ValueError, match="markdown_report missing backtest columns"):
        build_markdown_report(pd.DataFrame([{"net_pnl": 1.0}]))


@pytest.mark.integration
def test_markdown_report_contains_no_live_order_claim() -> None:
    history = require_lightweight_history("okx_1h_extended", "BTC_USDT_USDT_1h.parquet")
    data = load_symbol_file(history / "BTC_USDT_USDT_1h.parquet")
    trades = run_backtest(data.frame.head(600), inst_id="BTC-USDT-SWAP")
    report = build_markdown_report(trades)
    assert "SIGNAL_ONLY" in report
    assert "自动执行" not in report


@pytest.mark.integration
def test_latest_signal_payload_is_manual_only_json_safe() -> None:
    require_lightweight_history("okx_1h_extended", "BTC_USDT_USDT_1h.parquet")
    payload = latest_signal_payload(dataset="okx_1h_extended", symbol_file="BTC_USDT_USDT_1h.parquet", inst_id="BTC-USDT-SWAP")
    assert payload["live_order_enabled"] is False
    assert payload["mode"] == "signal_only"
    json.dumps(payload, default=str)
