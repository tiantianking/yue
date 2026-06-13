import json
from types import SimpleNamespace

import pandas as pd

from okx_signal_system.exchange.candles import okx_candles_to_frame
from okx_signal_system.training.startup_quality import (
    _select_symbols,
    is_latest_bar_fresh,
    load_selected_strategy_params,
)


def test_okx_candles_to_frame_accepts_nine_and_ten_field_rows() -> None:
    raw = [
        ["1760000000000", "100", "110", "90", "105", "12", "1200", "1200", "1"],
        ["1760003600000", "105", "112", "101", "108", "9", "900", "900", "1", "extra"],
    ]
    frame = okx_candles_to_frame(raw)
    assert list(frame.columns) == ["ts", "open", "high", "low", "close", "volume"]
    assert len(frame) == 2
    assert str(frame["ts"].dt.tz) == "UTC"
    assert frame.iloc[0]["volume"] == 12


def test_load_selected_strategy_params_reads_frozen_training_output(tmp_path) -> None:
    (tmp_path / "selected_params.json").write_text(
        json.dumps(
            {
                "fast_ema": 10,
                "slow_ema": 80,
                "breakout_window": 60,
                "atr_stop_mult": 1.5,
                "take_profit_mult": 2.0,
                "max_hold_bars": 24,
                "atr_window": 14,
            }
        ),
        encoding="utf-8",
    )
    params = load_selected_strategy_params(tmp_path)
    assert params.fast_ema == 10
    assert params.slow_ema == 80
    assert params.breakout_window == 60


def test_latest_bar_freshness_blocks_stale_history() -> None:
    now = pd.Timestamp("2026-06-13T12:00:00Z")
    fresh = pd.DataFrame({"ts": [now - pd.Timedelta(hours=2)]})
    stale = pd.DataFrame({"ts": [now - pd.Timedelta(hours=5)]})
    assert is_latest_bar_fresh(fresh, max_lag_hours=3.0, now=now)
    assert not is_latest_bar_fresh(stale, max_lag_hours=3.0, now=now)


def test_select_symbols_preserves_config_order() -> None:
    available = [
        SimpleNamespace(inst_id="ADA-USDT-SWAP"),
        SimpleNamespace(inst_id="BTC-USDT-SWAP"),
        SimpleNamespace(inst_id="ETH-USDT-SWAP"),
    ]
    selected = _select_symbols(available, ["BTC-USDT-SWAP", "ETH-USDT-SWAP", "ADA-USDT-SWAP"], max_symbols=2)
    assert [item.inst_id for item in selected] == ["BTC-USDT-SWAP", "ETH-USDT-SWAP"]
