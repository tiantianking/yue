import pandas as pd

from okx_signal_system.data.loader import closed_bars, file_symbol_to_inst_id, load_symbol_file
from okx_signal_system.data.quality import audit_symbol
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
