import pandas as pd
import pytest
import inspect

from okx_signal_system.exchange.position_monitor import (
    AutoStopMonitor,
    PositionRecord,
    PositionRecordStore,
    register_manual_position,
)
from okx_signal_system.strategy.ensemble import ensemble_vote
from okx_signal_system.strategy.trend_breakout import StrategyParams


def test_gui_runtime_dependencies_import() -> None:
    import gui  # noqa: F401
    import okx_signal_system.ml.pattern_recognition  # noqa: F401
    import okx_signal_system.ml.rolling_backtest  # noqa: F401

    assert "auto_start" in inspect.signature(gui.start_gui).parameters
    assert gui.OKXSignalGUI._breakout_gap_pct(None) is None


def test_websocket_client_uses_15m_candle_channel(monkeypatch) -> None:
    from okx_signal_system.exchange.realtime import OKXWebSocketClient

    monkeypatch.delenv("OKX_PUBLIC_WS_URL", raising=False)
    client = OKXWebSocketClient(timeframe="15m")
    assert client._candle_channel == "candle15m"
    assert client._get_wss_url() == "wss://ws.okx.com:8443/ws/v5/public"

    monkeypatch.setenv("OKX_PUBLIC_WS_URL", "wss://example.invalid/ws")
    assert client._get_wss_url() == "wss://example.invalid/ws"


def test_position_store_round_trip_and_validates_prices(tmp_path) -> None:
    store = PositionRecordStore(tmp_path)
    record = PositionRecord(
        inst_id="BTC-USDT-SWAP",
        side="long",
        entry_price=100.0,
        size=1.0,
        stop_loss=95.0,
        take_profit=110.0,
        leverage=5.0,
        entry_time=pd.Timestamp("2026-01-01T00:00:00Z").isoformat(),
    )
    store.save(record)
    assert store.load(record.key) == record

    with pytest.raises(ValueError):
        store.save(
            PositionRecord(
                inst_id="BTC-USDT-SWAP",
                side="long",
                entry_price=100.0,
                size=1.0,
                stop_loss=105.0,
                take_profit=110.0,
                leverage=5.0,
                entry_time=record.entry_time,
            )
        )


def test_auto_stop_price_trigger_logic() -> None:
    monitor = AutoStopMonitor(auto_close_enabled=False)
    long_record = PositionRecord(
        inst_id="BTC-USDT-SWAP",
        side="long",
        entry_price=100.0,
        size=1.0,
        stop_loss=95.0,
        take_profit=110.0,
        leverage=5.0,
        entry_time=pd.Timestamp("2026-01-01T00:00:00Z").isoformat(),
    )
    short_record = PositionRecord(
        inst_id="BTC-USDT-SWAP",
        side="short",
        entry_price=100.0,
        size=1.0,
        stop_loss=105.0,
        take_profit=90.0,
        leverage=5.0,
        entry_time=long_record.entry_time,
    )
    assert monitor._check_price(long_record, 94.9) == (True, "stop_loss")
    assert monitor._check_price(long_record, 110.1) == (True, "take_profit")
    assert monitor._check_price(short_record, 105.1) == (True, "stop_loss")
    assert monitor._check_price(short_record, 89.9) == (True, "take_profit")


def test_ensemble_vote_returns_bounded_score() -> None:
    frame = pd.DataFrame(
        {
            "ts": pd.date_range("2026-01-01", periods=25, tz="UTC", freq="h"),
            "open": [100.0] * 25,
            "high": [112.0] * 25,
            "low": [95.0] * 25,
            "close": [110.0] * 25,
            "atr": [2.0] * 25,
            "atr_pct": [0.02] * 25,
            "bias_4h": ["long"] * 25,
            "breakout_high": [100.0] * 25,
            "breakout_low": [90.0] * 25,
            "ema_fast": [112.0] * 25,
            "ema_slow": [108.0] * 25,
            "vol_ratio": [1.2] * 25,
        }
    )
    result = ensemble_vote(frame.iloc[-1], StrategyParams(), frame, len(frame) - 1, base_score=6.0)
    assert result.final_side in {"long", "short", "flat"}
    assert 1.0 <= result.final_score <= 10.0
    assert len(result.votes) == 4
