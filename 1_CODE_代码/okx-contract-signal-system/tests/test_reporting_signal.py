import json

from okx_signal_system.backtest.runner import run_backtest
from okx_signal_system.data.loader import load_symbol_file
from okx_signal_system.paths import find_lightweight_history
from okx_signal_system.reporting.report_builder import build_markdown_report
from okx_signal_system.signal_service.job import latest_signal_payload


def test_markdown_report_contains_no_live_order_claim() -> None:
    data = load_symbol_file(find_lightweight_history("okx_1h_extended") / "BTC_USDT_USDT_1h.parquet")
    trades = run_backtest(data.frame.head(600), inst_id="BTC-USDT-SWAP")
    report = build_markdown_report(trades)
    assert "不自动实盘下单" in report


def test_latest_signal_payload_is_manual_only_json_safe() -> None:
    payload = latest_signal_payload(dataset="okx_1h_extended", symbol_file="BTC_USDT_USDT_1h.parquet", inst_id="BTC-USDT-SWAP")
    assert payload["live_order_enabled"] is False
    assert payload["mode"] == "manual_confirmation_only"
    json.dumps(payload, default=str)
