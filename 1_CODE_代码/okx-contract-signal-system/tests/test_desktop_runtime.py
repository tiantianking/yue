import pandas as pd
import pytest
import inspect
import asyncio

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
    assert client._get_wss_url() == "wss://ws.okx.com:8443/ws/v5/business"

    monkeypatch.setenv("OKX_PUBLIC_WS_URL", "wss://example.invalid/ws")
    assert client._get_wss_url() == "wss://example.invalid/ws"


def test_websocket_message_parses_okx_millisecond_timestamp() -> None:
    from okx_signal_system.exchange.realtime import OKXWebSocketClient

    candles = []
    client = OKXWebSocketClient(on_candle=lambda inst_id, candle: candles.append((inst_id, candle)))
    client._handle_message(
        {
            "arg": {"channel": "candle15m", "instId": "BTC-USDT-SWAP"},
            "data": [
                [
                    "1781546400000",
                    "66784.3",
                    "66845.5",
                    "66633.8",
                    "66828.3",
                    "88823.72",
                    "888.2372",
                    "59271158.11091000",
                    "0",
                ]
            ],
        }
    )

    assert candles
    assert candles[0][0] == "BTC-USDT-SWAP"
    assert candles[0][1]["ts"] == pd.Timestamp("2026-06-15T18:00:00Z")
    assert candles[0][1]["is_closed"] is False


def test_websocket_proxy_options_parse_env(monkeypatch) -> None:
    from okx_signal_system.exchange import realtime

    monkeypatch.setenv("OKX_WS_PROXY", "http://user:pass@127.0.0.1:1088")
    assert realtime._okx_ws_proxy_url() == "http://user:pass@127.0.0.1:1088"
    assert realtime._websocket_proxy_options(realtime._okx_ws_proxy_url()) == {
        "http_proxy_host": "127.0.0.1",
        "http_proxy_port": 1088,
        "proxy_type": "http",
        "http_proxy_timeout": 8,
        "http_proxy_auth": ("user", "pass"),
    }

    monkeypatch.setenv("OKX_WS_PROXY", "off")
    assert realtime._okx_ws_proxy_url() is None


def test_websocket_reconnect_does_not_self_disable(monkeypatch) -> None:
    from okx_signal_system.exchange import realtime
    from okx_signal_system.exchange.realtime import OKXWebSocketClient

    client = OKXWebSocketClient(timeframe="15m")
    client._running = True
    attempts = []

    def fail_once():
        attempts.append(True)
        client._running = False
        raise RuntimeError("temporary websocket failure")

    monkeypatch.setattr(realtime.time, "sleep", lambda _delay: None)
    monkeypatch.setattr(client, "_start_websocket", fail_once)

    client._handle_disconnect()

    assert attempts
    assert client._degraded
    assert client._last_error == "temporary websocket failure"


def test_realtime_api_reports_failed_websocket_connect(monkeypatch, tmp_path) -> None:
    from okx_signal_system.exchange import realtime

    monkeypatch.setattr(realtime, "find_lightweight_history", lambda _dataset: tmp_path)
    monkeypatch.setattr(realtime, "test_connection", lambda: {"connected": True})
    monkeypatch.setattr(realtime.OKXWebSocketClient, "connect", lambda self, symbols: False)

    api = realtime.OKXRealtimeAPI(
        {
            "data": {
                "timeframe": "15m",
                "trend_timeframe": "1h",
                "historical_dataset": "test",
                "symbols": ["BTC-USDT-SWAP"],
            }
        }
    )

    assert not asyncio.run(api.connect(["BTC-USDT-SWAP"]))
    assert not api.is_connected()


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
