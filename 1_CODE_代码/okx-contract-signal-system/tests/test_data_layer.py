import pandas as pd
from pathlib import Path

from okx_signal_system.data.closed_backfill import latest_closed_candle_start, seconds_until_next_closed_run
from okx_signal_system.data.gap_handler import DataGap, DataGapHandler, summarize_sync_error
from okx_signal_system.data.loader import closed_bars, file_symbol_to_inst_id, file_timeframe, load_symbol_file
from okx_signal_system.data.quality import audit_symbol
from okx_signal_system.exchange.realtime import RealtimeDataStore
from okx_signal_system.paths import find_lightweight_history
from okx_signal_system.timeframe import bars_for_hours, default_trend_timeframe, timeframe_spec


def test_find_okx_history_root() -> None:
    root = find_lightweight_history("okx_1h_extended")
    assert (root / "BTC_USDT_USDT_1h.parquet").exists()


def test_file_symbol_maps_to_okx_inst_id() -> None:
    assert file_symbol_to_inst_id(find_lightweight_history("okx_1h_extended") / "BTC_USDT_USDT_1h.parquet") == "BTC-USDT-SWAP"


def test_list_parquet_files_ignores_atomic_tmp_files(tmp_path, monkeypatch) -> None:
    from okx_signal_system.data import loader

    (tmp_path / "BTC_USDT_USDT_15m.parquet").write_text("")
    (tmp_path / "BTC_USDT_USDT_15m.123.tmp.parquet").write_text("")
    monkeypatch.setattr(loader, "find_lightweight_history", lambda _dataset: tmp_path)

    assert [path.name for path in loader.list_parquet_files("x")] == ["BTC_USDT_USDT_15m.parquet"]


def test_timeframe_helpers_support_15m_signal_mode() -> None:
    spec = timeframe_spec("15m")
    assert spec.okx_bar == "15m"
    assert spec.ws_channel == "candle15m"
    assert default_trend_timeframe("15m") == "1h"
    assert bars_for_hours(24, "15m") == 96
    assert file_timeframe(Path("HYPE_USDT_USDT_15m.parquet")) == "15m"


def test_closed_backfill_waits_for_confirmed_bar() -> None:
    now = pd.Timestamp("2026-06-13T21:37:00Z")
    assert latest_closed_candle_start("15m", now=now, settle_seconds=60) == pd.Timestamp("2026-06-13T21:15:00Z")
    assert 7 * 60 < seconds_until_next_closed_run("15m", now=now, settle_seconds=60) < 10 * 60


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
    store = RealtimeDataStore(tmp_path, timeframe="15m")
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
    assert (tmp_path / "ADA_USDT_USDT_15m.parquet").name == store._get_file_path("ADA-USDT-SWAP").name


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


def test_gap_merge_fills_optional_metadata(tmp_path) -> None:
    handler = DataGapHandler(tmp_path)
    frame = pd.DataFrame(
        {
            "ts": [pd.Timestamp("2026-06-13T10:00:00Z")],
            "open": [10.0],
            "high": [11.0],
            "low": [9.0],
            "close": [10.5],
            "volume": [100.0],
            "quote_volume": [1000.0],
        }
    )

    assert handler.merge_and_save("HYPE-USDT-SWAP", frame, mode="replace")
    saved = pd.read_parquet(tmp_path / "HYPE_USDT_USDT_1h.parquet")
    assert saved.iloc[0]["symbol"] == "HYPE-USDT-SWAP"
    assert saved.iloc[0]["timeframe"] == "1h"
    assert bool(saved.iloc[0]["is_closed"]) is True


def test_backfill_uses_detected_gap_boundaries(tmp_path, monkeypatch) -> None:
    calls = []

    def fake_get_candles(inst_id, bar, limit, *, before=None, after=None):
        calls.append({"inst_id": inst_id, "bar": bar, "limit": limit, "before": before, "after": after})
        return [
            ["1769907600000", "10", "11", "9", "10.5", "1", "10", "10", "1"],
            ["1769911200000", "11", "12", "10", "11.5", "1", "11", "11", "1"],
        ]

    monkeypatch.setattr("okx_signal_system.data.gap_handler.get_candles", fake_get_candles)
    handler = DataGapHandler(tmp_path)
    gap = DataGap(
        inst_id="ETC-USDT-SWAP",
        start_time=pd.Timestamp("2026-02-01T00:00:00Z").to_pydatetime(),
        end_time=pd.Timestamp("2026-02-01T03:00:00Z").to_pydatetime(),
        missing_bars=3,
        severity="moderate",
    )

    frame = handler.backfill_gap(gap)

    assert frame is not None
    assert calls[0]["before"] == "1769904000000"
    assert calls[0]["after"] == "1769914800000"
    assert list(frame["ts"].dt.hour) == [1, 2]


def test_sync_error_summary_shortens_dns_errors() -> None:
    raw = (
        "OKX network error: HTTPSConnectionPool(host='www.okx.com', port=443): "
        "Caused by NameResolutionError(\"Failed to resolve 'www.okx.com'\")"
    )
    assert summarize_sync_error(raw) == "OKX REST DNS解析失败：www.okx.com；已继续使用本地历史数据和WebSocket"
