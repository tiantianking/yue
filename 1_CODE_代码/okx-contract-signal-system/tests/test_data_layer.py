import hashlib

import pandas as pd
from pathlib import Path

import pytest

from tests._integration import require_lightweight_history
from okx_signal_system.data.closed_backfill import (
    ClosedCandleBackfillService,
    latest_closed_candle_start,
    seconds_until_next_closed_run,
)
from okx_signal_system.data.gap_handler import DataGap, DataGapHandler, summarize_sync_error
from okx_signal_system.data.loader import SymbolData, closed_bars, file_symbol_to_inst_id, file_timeframe, load_symbol_file
from okx_signal_system.data.quality import audit_symbol
from okx_signal_system.exchange.realtime import RealtimeDataStore
from okx_signal_system.paths import find_lightweight_history
from okx_signal_system.timeframe import bars_for_hours, default_trend_timeframe, timeframe_spec


def _valid_15m_frame(rows: int = 8) -> pd.DataFrame:
    ts = pd.date_range("2026-06-15T00:00:00Z", periods=rows, freq="15min", tz="UTC")
    return pd.DataFrame(
        {
            "ts": ts,
            "open": [100.0] * rows,
            "high": [101.0] * rows,
            "low": [99.0] * rows,
            "close": [100.5] * rows,
            "volume": [10.0] * rows,
            "quote_volume": [1000.0] * rows,
            "symbol": ["BTC-USDT-SWAP"] * rows,
            "timeframe": ["15m"] * rows,
            "is_closed": [True] * rows,
        }
    )


def _symbol_data(frame: pd.DataFrame) -> SymbolData:
    return SymbolData(
        inst_id="BTC-USDT-SWAP",
        source_path=Path("BTC_USDT_USDT_15m.parquet"),
        frame=frame,
    )


def _closed_backfill_frame(timestamps: list[pd.Timestamp] | pd.DatetimeIndex) -> pd.DataFrame:
    rows = len(timestamps)
    return pd.DataFrame(
        {
            "ts": list(timestamps),
            "open": [100.0] * rows,
            "high": [101.0] * rows,
            "low": [99.0] * rows,
            "close": [100.5] * rows,
            "volume": [10.0] * rows,
            "quote_volume": [1000.0] * rows,
            "symbol": ["BTC-USDT-SWAP"] * rows,
            "timeframe": ["15m"] * rows,
            "is_closed": [True] * rows,
        }
    )


def _set_nan_close(frame: pd.DataFrame) -> None:
    frame.loc[frame.index[1], "close"] = float("nan")


def _set_inf_high(frame: pd.DataFrame) -> None:
    frame.loc[frame.index[1], "high"] = float("inf")


def _shift_one_timestamp_off_boundary(frame: pd.DataFrame) -> None:
    frame.loc[frame.index[3], "ts"] = frame.loc[frame.index[3], "ts"] + pd.Timedelta(minutes=1)


def _drop_internal_bar(frame: pd.DataFrame) -> None:
    frame.drop(index=frame.index[3], inplace=True)


def _break_ohlc(frame: pd.DataFrame) -> None:
    frame.loc[frame.index[1], "high"] = 99.0


def _change_symbol(frame: pd.DataFrame) -> None:
    frame.loc[frame.index[1], "symbol"] = "ETH-USDT-SWAP"


def _change_timeframe(frame: pd.DataFrame) -> None:
    frame.loc[frame.index[1], "timeframe"] = "1h"


def _break_quote_volume(frame: pd.DataFrame) -> None:
    frame.loc[frame.index[1], "quote_volume"] = -1.0


@pytest.mark.integration
def test_find_okx_history_root() -> None:
    root = require_lightweight_history("okx_1h_extended", "BTC_USDT_USDT_1h.parquet")
    assert (root / "BTC_USDT_USDT_1h.parquet").exists()


def test_find_history_uses_packaged_data_root(tmp_path, monkeypatch) -> None:
    dataset = "okx_15m_extended"
    packaged_root = tmp_path / "_MEIPASS"
    expected = packaged_root / "lightweight_history" / dataset
    expected.mkdir(parents=True)

    monkeypatch.delenv("JIAOYI_DATA_DIR", raising=False)
    monkeypatch.setattr("okx_signal_system.paths._data_root_from_config", lambda: None)
    monkeypatch.setattr("okx_signal_system.paths.workspace_root", lambda start=None: tmp_path / "workspace")
    monkeypatch.setattr("sys.frozen", True, raising=False)
    monkeypatch.setattr("sys._MEIPASS", str(packaged_root), raising=False)

    assert find_lightweight_history(dataset) == expected


def test_find_history_uses_onedir_internal_data_root(tmp_path, monkeypatch) -> None:
    dataset = "okx_15m_extended"
    exe_dir = tmp_path / "dist" / "OKXSignalSystem"
    expected = exe_dir / "_internal" / "lightweight_history" / dataset
    expected.mkdir(parents=True)

    monkeypatch.delenv("JIAOYI_DATA_DIR", raising=False)
    monkeypatch.setattr("okx_signal_system.paths._data_root_from_config", lambda: None)
    monkeypatch.setattr("okx_signal_system.paths.workspace_root", lambda start=None: tmp_path / "workspace")
    monkeypatch.setattr("sys.frozen", True, raising=False)
    monkeypatch.setattr("sys.executable", str(exe_dir / "OKXSignalSystem.exe"))
    monkeypatch.delattr("sys._MEIPASS", raising=False)

    assert find_lightweight_history(dataset) == expected


@pytest.mark.integration
def test_file_symbol_maps_to_okx_inst_id() -> None:
    history = require_lightweight_history("okx_1h_extended", "BTC_USDT_USDT_1h.parquet")
    assert file_symbol_to_inst_id(history / "BTC_USDT_USDT_1h.parquet") == "BTC-USDT-SWAP"


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


@pytest.mark.integration
def test_load_symbol_file_normalizes_columns() -> None:
    history = require_lightweight_history("okx_1h_extended", "BTC_USDT_USDT_1h.parquet")
    data = load_symbol_file(history / "BTC_USDT_USDT_1h.parquet")
    assert data.inst_id == "BTC-USDT-SWAP"
    assert {"ts", "open", "high", "low", "close", "volume", "is_closed"}.issubset(data.frame.columns)
    assert str(data.frame["ts"].dt.tz) == "UTC"


def test_closed_bars_filters_unclosed_rows() -> None:
    frame = pd.DataFrame({"ts": pd.date_range("2026-01-01", periods=2, tz="UTC"), "is_closed": [True, False]})
    assert len(closed_bars(frame)) == 1


def test_closed_bars_parses_string_closed_flags() -> None:
    frame = pd.DataFrame(
        {
            "ts": pd.date_range("2026-01-01", periods=6, tz="UTC"),
            "is_closed": ["True", "False", "1", "0", "yes", "no"],
        }
    )

    result = closed_bars(frame)

    assert list(result["is_closed"]) == ["True", "1", "yes"]


def test_quality_audit_fails_formal_history_when_any_row_is_open() -> None:
    frame = _valid_15m_frame()
    frame["is_closed"] = False

    result = audit_symbol(_symbol_data(frame), expected_freq="15m")

    assert result.status == "failed"
    assert result.open_rows == len(frame)


def test_quality_audit_allows_only_runtime_tail_open_row() -> None:
    frame = _valid_15m_frame()
    frame.loc[frame.index[-1], "is_closed"] = False

    formal = audit_symbol(_symbol_data(frame), expected_freq="15m")
    runtime = audit_symbol(_symbol_data(frame), expected_freq="15m", allow_runtime_open_tail=True)

    assert formal.status == "failed"
    assert runtime.status == "passed"
    assert runtime.open_rows == 1
    assert runtime.non_tail_open_rows == 0


def test_quality_audit_rejects_non_tail_runtime_open_rows() -> None:
    frame = _valid_15m_frame()
    frame.loc[frame.index[2], "is_closed"] = False

    result = audit_symbol(_symbol_data(frame), expected_freq="15m", allow_runtime_open_tail=True)

    assert result.status == "failed"
    assert result.open_rows == 1
    assert result.non_tail_open_rows == 1


@pytest.mark.parametrize(
    ("mutate", "field"),
    [
        (_set_nan_close, "invalid_numeric_rows"),
        (_set_inf_high, "invalid_numeric_rows"),
        (_shift_one_timestamp_off_boundary, "timestamp_boundary_rows"),
        (_drop_internal_bar, "internal_gap_count"),
        (_break_ohlc, "invalid_ohlc_rows"),
        (_change_symbol, "symbol_mismatch_rows"),
        (_change_timeframe, "timeframe_mismatch_rows"),
        (_break_quote_volume, "invalid_quote_volume_rows"),
    ],
)
def test_quality_audit_rejects_structural_and_value_errors(mutate, field) -> None:
    frame = _valid_15m_frame()
    mutate(frame)

    result = audit_symbol(_symbol_data(frame), expected_freq="15m")

    assert result.status == "failed"
    assert getattr(result, field) > 0


def test_find_lightweight_history_uses_env_data_root(tmp_path, monkeypatch) -> None:
    data_root = tmp_path / "data"
    dataset_root = data_root / "lightweight_history" / "okx_15m_extended"
    dataset_root.mkdir(parents=True)

    monkeypatch.setenv("JIAOYI_DATA_DIR", str(data_root))

    assert find_lightweight_history("okx_15m_extended") == dataset_root


def test_find_lightweight_history_uses_explicit_root_dir(tmp_path, monkeypatch) -> None:
    env_root = tmp_path / "env_data"
    explicit_root = tmp_path / "explicit_data"
    dataset_root = explicit_root / "lightweight_history" / "okx_15m_extended"
    dataset_root.mkdir(parents=True)

    monkeypatch.setenv("JIAOYI_DATA_DIR", str(env_root))

    assert find_lightweight_history("okx_15m_extended", root_dir=explicit_root) == dataset_root


def test_gap_handler_uses_configured_history_root(tmp_path, monkeypatch) -> None:
    data_root = tmp_path / "data"
    dataset_root = data_root / "lightweight_history" / "okx_15m_extended"
    dataset_root.mkdir(parents=True)

    monkeypatch.setenv("JIAOYI_DATA_DIR", str(data_root))

    handler = DataGapHandler(timeframe="15m")

    assert handler.data_dir == dataset_root


def test_gap_handler_respects_read_only_guard(tmp_path) -> None:
    path = tmp_path / "BTC_USDT_USDT_15m.parquet"
    pd.DataFrame(
        {
            "ts": [pd.Timestamp("2026-06-15T18:00:00Z")],
            "open": [100.0],
            "high": [101.0],
            "low": [99.0],
            "close": [100.5],
            "volume": [10.0],
            "quote_volume": [1000.0],
        }
    ).to_parquet(path, index=False)
    before_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    before_mtime = path.stat().st_mtime_ns

    handler = DataGapHandler(tmp_path, timeframe="15m", read_only=True)
    result = handler.merge_and_save(
        "BTC-USDT-SWAP",
        pd.DataFrame(
            {
                "ts": [pd.Timestamp("2026-06-15T18:15:00Z")],
                "open": [101.0],
                "high": [102.0],
                "low": [100.0],
                "close": [101.5],
                "volume": [12.0],
                "quote_volume": [1200.0],
            }
        ),
        mode="merge",
    )

    assert not result
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before_hash
    assert path.stat().st_mtime_ns == before_mtime


def test_closed_backfill_service_writes_runtime_cache_without_mutating_history(tmp_path, monkeypatch) -> None:
    dataset = "okx_15m_extended"
    history_root = tmp_path / "history" / "lightweight_history" / dataset
    runtime_root = tmp_path / "runtime" / "lightweight_history" / dataset
    history_root.mkdir(parents=True)
    history_path = history_root / "BTC_USDT_USDT_15m.parquet"
    pd.DataFrame(
        {
            "ts": [pd.Timestamp("2026-06-15T18:00:00Z")],
            "open": [100.0],
            "high": [101.0],
            "low": [99.0],
            "close": [100.0],
            "volume": [10.0],
            "quote_volume": [1000.0],
        }
    ).to_parquet(history_path, index=False)
    before_hash = hashlib.sha256(history_path.read_bytes()).hexdigest()
    before_mtime = history_path.stat().st_mtime_ns

    def fail_history_lookup(*args, **kwargs):
        raise AssertionError("history lookup not allowed")

    monkeypatch.setattr("okx_signal_system.data.gap_handler.find_lightweight_history", fail_history_lookup)
    monkeypatch.setattr("okx_signal_system.data.closed_backfill.find_runtime_cache_root", lambda _dataset: runtime_root)
    monkeypatch.setattr(
        "okx_signal_system.data.closed_backfill.latest_closed_candle_start",
        lambda *args, **kwargs: pd.Timestamp("2026-06-15T18:15:00Z").to_pydatetime(),
    )
    monkeypatch.setattr(
        "okx_signal_system.data.closed_backfill.seconds_until_next_closed_run",
        lambda *args, **kwargs: 60.0,
    )

    def fake_get_candles(inst_id, bar, limit):
        return [
            ["1781546400000", "100", "101", "99", "100", "10", "1000", "1000", "1"],
            ["1781547300000", "100.5", "102", "100", "101.5", "12", "1200", "1200", "1"],
        ]

    monkeypatch.setattr("okx_signal_system.data.closed_backfill.get_candles", fake_get_candles)

    from okx_signal_system.data.closed_backfill import ClosedCandleBackfillService

    service = ClosedCandleBackfillService(
        ["BTC-USDT-SWAP"],
        timeframe="15m",
        dataset=dataset,
        settle_seconds=60,
        output_path=tmp_path / "status.json",
    )
    status = service.run_once()

    runtime_path = runtime_root / "BTC_USDT_USDT_15m.parquet"
    assert service.data_dir == runtime_root
    assert status.all_complete
    assert runtime_path.exists()
    runtime_frame = pd.read_parquet(runtime_path)
    assert len(runtime_frame) == 2
    assert hashlib.sha256(history_path.read_bytes()).hexdigest() == before_hash
    assert history_path.stat().st_mtime_ns == before_mtime


@pytest.mark.parametrize("gap_bars", [1, 2, 10, 180, 500])
def test_closed_backfill_service_blocks_all_complete_on_internal_gaps(tmp_path, monkeypatch, gap_bars) -> None:
    expected = pd.Timestamp("2026-06-15T18:00:00Z")
    full_range = pd.date_range(end=expected, periods=620, freq="15min", tz="UTC")
    gap_start = len(full_range) - gap_bars - 20
    kept = full_range.delete(range(gap_start, gap_start + gap_bars))
    (_closed_backfill_frame(kept)).to_parquet(tmp_path / "BTC_USDT_USDT_15m.parquet", index=False)

    monkeypatch.setattr(
        "okx_signal_system.data.closed_backfill.latest_closed_candle_start",
        lambda *args, **kwargs: expected.to_pydatetime(),
    )
    monkeypatch.setattr(
        "okx_signal_system.data.closed_backfill.seconds_until_next_closed_run",
        lambda *args, **kwargs: 60.0,
    )
    monkeypatch.setattr("okx_signal_system.data.closed_backfill.get_candles", lambda *args, **kwargs: [])

    service = ClosedCandleBackfillService(
        ["BTC-USDT-SWAP"],
        timeframe="15m",
        dataset="okx_15m_extended",
        data_dir=tmp_path,
        output_path=tmp_path / "status.json",
        required_history_bars=100,
        minimum_continuous_tail_bars=20,
    )
    status = service.run_once()
    row = status.symbols[0]

    assert not status.all_complete
    assert row.status == "gapped"
    assert row.internal_gap_count == 1
    assert row.max_gap_bars == gap_bars
    assert row.continuous_tail_bars >= 20


def test_closed_backfill_service_blocks_all_complete_on_short_continuous_tail(tmp_path, monkeypatch) -> None:
    expected = pd.Timestamp("2026-06-15T18:00:00Z")
    timestamps = pd.date_range(end=expected, periods=20, freq="15min", tz="UTC")
    (_closed_backfill_frame(timestamps)).to_parquet(tmp_path / "BTC_USDT_USDT_15m.parquet", index=False)

    monkeypatch.setattr(
        "okx_signal_system.data.closed_backfill.latest_closed_candle_start",
        lambda *args, **kwargs: expected.to_pydatetime(),
    )
    monkeypatch.setattr(
        "okx_signal_system.data.closed_backfill.seconds_until_next_closed_run",
        lambda *args, **kwargs: 60.0,
    )
    monkeypatch.setattr("okx_signal_system.data.closed_backfill.get_candles", lambda *args, **kwargs: [])

    service = ClosedCandleBackfillService(
        ["BTC-USDT-SWAP"],
        timeframe="15m",
        dataset="okx_15m_extended",
        data_dir=tmp_path,
        output_path=tmp_path / "status.json",
        required_history_bars=30,
        minimum_continuous_tail_bars=30,
    )
    status = service.run_once()
    row = status.symbols[0]

    assert not status.all_complete
    assert row.status == "insufficient_history"
    assert row.internal_gap_count == 0
    assert row.continuous_tail_bars == 20
    assert row.minimum_continuous_tail == 30
    assert row.required_history_bars == 30


@pytest.mark.integration
def test_quality_audit_passes_btc_history() -> None:
    history = require_lightweight_history("okx_1h_extended", "BTC_USDT_USDT_1h.parquet")
    data = load_symbol_file(history / "BTC_USDT_USDT_1h.parquet")
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


def test_realtime_store_overwrites_same_bar_without_dtype_error(tmp_path) -> None:
    store = RealtimeDataStore(tmp_path, timeframe="15m")
    inst_id = "BTC-USDT-SWAP"
    store._cache[inst_id] = pd.DataFrame(
        {
            "ts": [pd.Timestamp("2026-06-15T18:00:00Z")],
            "open": [66784.3],
            "high": [66845.5],
            "low": [66633.8],
            "close": [66828.3],
            "volume": [88823.72],
            "quote_volume": [59271158.11091],
            "symbol": [inst_id],
            "timeframe": ["15m"],
            "is_closed": [False],
        }
    )

    store.append_candle(
        inst_id,
        {
            "ts": pd.Timestamp("2026-06-15T18:00:00Z"),
            "open": 66784.3,
            "high": 66850.0,
            "low": 66633.8,
            "close": 66840.0,
            "volume": 90035.49768,
            "quote_volume": 60000000.0,
            "is_closed": False,
        },
    )

    frame = store.load(inst_id)
    assert len(frame) == 1
    assert frame.iloc[-1]["close"] == 66840.0
    assert frame.iloc[-1]["volume"] == 90035.49768


@pytest.mark.parametrize(
    "cached_frame",
    [
        pd.DataFrame(columns=["ts", "open", "high", "low", "close", "volume", "quote_volume"]),
        pd.DataFrame(
            {
                "ts": [pd.NaT],
                "open": [pd.NA],
                "high": [pd.NA],
                "low": [pd.NA],
                "close": [pd.NA],
                "volume": [pd.NA],
                "quote_volume": [pd.NA],
            }
        ),
    ],
)
def test_realtime_store_appends_to_empty_or_all_na_cache_without_concat(tmp_path, monkeypatch, cached_frame) -> None:
    store = RealtimeDataStore(tmp_path, timeframe="15m")
    inst_id = "SOL-USDT-SWAP"
    if not cached_frame.empty or list(cached_frame.columns):
        store._cache[inst_id] = cached_frame

    def fail_concat(*args, **kwargs):
        raise AssertionError("pd.concat should not be used for empty or all-NA live caches")

    monkeypatch.setattr("okx_signal_system.exchange.realtime.pd.concat", fail_concat)

    store.append_candle(
        inst_id,
        {
            "ts": "2026-06-15T18:00:00Z",
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.5,
            "volume": 10.0,
            "quote_volume": 1000.0,
        },
    )

    frame = store.load(inst_id)
    assert len(frame) == 1
    assert frame.iloc[-1]["close"] == 100.5


def test_realtime_store_writes_runtime_cache_without_mutating_history(tmp_path) -> None:
    history = tmp_path / "history"
    runtime = tmp_path / "runtime"
    history.mkdir()
    history_path = history / "BTC_USDT_USDT_15m.parquet"
    pd.DataFrame(
        {
            "ts": [pd.Timestamp("2026-06-15T18:00:00Z")],
            "open": [100.0],
            "high": [101.0],
            "low": [99.0],
            "close": [100.0],
            "volume": [10.0],
            "quote_volume": [1000.0],
        }
    ).to_parquet(history_path, index=False)

    store = RealtimeDataStore(
        timeframe="15m",
        historical_data_dir=history,
        runtime_cache_dir=runtime,
        max_cache_bars=3500,
    )
    store.append_candle(
        "BTC-USDT-SWAP",
        {
            "ts": "2026-06-15T18:15:00Z",
            "open": 101.0,
            "high": 102.0,
            "low": 100.0,
            "close": 101.5,
            "volume": 20.0,
            "quote_volume": 2000.0,
        },
    )

    assert store.save("BTC-USDT-SWAP")
    history_frame = pd.read_parquet(history_path)
    runtime_frame = pd.read_parquet(runtime / "BTC_USDT_USDT_15m.parquet")
    assert len(history_frame) == 1
    assert len(runtime_frame) == 2
    assert pd.to_datetime(runtime_frame["ts"], utc=True).max() == pd.Timestamp("2026-06-15T18:15:00Z")


def test_realtime_store_retains_at_least_3500_bars(tmp_path) -> None:
    store = RealtimeDataStore(tmp_path, timeframe="15m")
    inst_id = "ETH-USDT-SWAP"

    for ts in pd.date_range("2026-01-01", periods=3600, freq="15min", tz="UTC"):
        store.append_candle(
            inst_id,
            {
                "ts": ts,
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.5,
                "volume": 10.0,
                "quote_volume": 1000.0,
            },
        )

    frame = store.load(inst_id)
    assert len(frame) == store.max_cache_bars
    assert len(frame) >= 3500
    assert frame["ts"].iloc[0] == pd.Timestamp("2026-01-02T00:00:00Z")


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
