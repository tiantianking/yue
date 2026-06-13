import pandas as pd

from okx_signal_system.data.gap_handler import DataGapHandler, summarize_sync_error
from okx_signal_system.data.loader import closed_bars, file_symbol_to_inst_id, load_symbol_file
from okx_signal_system.data.quality import audit_symbol
from okx_signal_system.exchange.realtime import RealtimeDataStore
from okx_signal_system.paths import find_lightweight_history


def test_find_okx_history_root() -> None:
    root = find_lightweight_history("okx_1h_extended")
    assert (root / "BTC_USDT_USDT_1h.parquet").exists()


def test_file_symbol_maps_to_okx_inst_id() -> None:
    assert file_symbol_to_inst_id(find_lightweight_history("okx_1h_extended") / "BTC_USDT_USDT_1h.parquet") == "BTC-USDT-SWAP"


def test_load_symbol_file_normalizes_columns() -> None:
    data = load_symbol_file(find_lightweight_history("okx_1h_extended") / "BTC_USDT_USDT_1h.parquet")
    assert data.inst_id == "BTC-USDT-SWAP"
    assert {"ts", "open", "high", "low", "close", "volume", "is_closed"}.issubset(data.frame.columns)
    assert str(data.frame["ts"].dt.tz) == "UTC"


def test_closed_bars_filters_unclosed_rows() -> None:
    frame = pd.DataFrame({"ts": pd.date_range("2026-01-01", periods=2, tz="UTC"), "is_closed": [True, False]})
    assert len(closed_bars(frame)) == 1


def test_quality_audit_passes_btc_history() -> None:
    data = load_symbol_file(find_lightweight_history("okx_1h_extended") / "BTC_USDT_USDT_1h.parquet")
    result = audit_symbol(data)
    assert result.status == "passed"
    assert result.duplicate_ts == 0
    assert result.invalid_ohlc_rows == 0


def test_realtime_store_preserves_quote_volume(tmp_path) -> None:
    store = RealtimeDataStore(tmp_path)
    store.append_candle(
        "ADA-USDT-SWAP",
        {
            "ts": "2026-06-13T10:00:00Z",
            "open": 0.6,
            "high": 0.7,
            "low": 0.5,
            "close": 0.65,
            "volume": 1000,
            "quote_volume": 10000,
        },
    )
    frame = store.load("ADA-USDT-SWAP")
    assert frame.iloc[-1]["quote_volume"] == 10000


def test_gap_sync_stops_batch_after_rest_unavailable(tmp_path, monkeypatch) -> None:
    stale = pd.DataFrame(
        {
            "ts": [pd.Timestamp.now("UTC") - pd.Timedelta(days=4)],
            "open": [100.0],
            "high": [101.0],
            "low": [99.0],
            "close": [100.0],
            "volume": [10.0],
            "quote_volume": [1000.0],
        }
    )
    for symbol in ["BTC", "ETH"]:
        stale.to_parquet(tmp_path / f"{symbol}_USDT_USDT_1h.parquet", index=False)

    calls = []

    def fail_get_candles(*args, **kwargs):
        calls.append(args)
        raise ConnectionError("dns unavailable")

    monkeypatch.setattr("okx_signal_system.data.gap_handler.get_candles", fail_get_candles)
    handler = DataGapHandler(tmp_path)
    results = handler.sync_all_symbols(["BTC-USDT-SWAP", "ETH-USDT-SWAP"])

    assert len(calls) == 1
    assert not results["BTC-USDT-SWAP"].success
    assert not results["ETH-USDT-SWAP"].success
    assert "dns unavailable" in results["ETH-USDT-SWAP"].errors[0]


def test_sync_error_summary_shortens_dns_errors() -> None:
    raw = (
        "OKX network error: HTTPSConnectionPool(host='www.okx.com', port=443): "
        "Caused by NameResolutionError(\"Failed to resolve 'www.okx.com'\")"
    )
    assert summarize_sync_error(raw) == "OKX REST DNS解析失败：www.okx.com；已继续使用本地历史数据和WebSocket"
