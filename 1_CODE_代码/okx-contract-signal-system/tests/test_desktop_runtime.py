import pandas as pd
import pytest
import inspect
import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import okx_signal_system
from okx_signal_system.exchange.position_monitor import (
    AutoStopMonitor,
    PositionRecord,
    PositionRecordStore,
    register_manual_position,
)
from okx_signal_system.strategy.ensemble import ensemble_vote
from okx_signal_system.strategy.trend_breakout import StrategyParams
from okx_signal_system.strategy.vote_gate import min_vote_approval_rate, vote_gate_passed


def test_runtime_health_reports_stale_dashboard_5m_backfill_as_warning(tmp_path, monkeypatch) -> None:
    from scripts import system_check

    symbol = "BTC-USDT-SWAP"
    now = datetime.now(timezone.utc)
    status_path = tmp_path / "latest_scan_status.json"
    status_path.write_text(
        json.dumps(
            {
                "generated_at": now.isoformat(),
                "status": "running",
                "error": None,
                "websocket": {
                    "connected": True,
                    "degraded": False,
                    "subscriptions": [symbol],
                },
                "symbols": [{"symbol": symbol}],
                "lifecycle_summary": {"outbox": {}},
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "closed_kline_backfill_status.json").write_text(
        json.dumps(
            {
                "generated_at": now.isoformat(),
                "all_complete": True,
                "symbols_checked": 1,
                "write_failures": 0,
                "expected_latest_closed": now.isoformat(),
                "symbols": [{"inst_id": symbol, "status": "passed"}],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "closed_kline_backfill_status_5m.json").write_text(
        json.dumps(
            {
                "generated_at": (now - timedelta(days=1)).isoformat(),
                "all_complete": True,
                "symbols_checked": 1,
                "write_failures": 0,
                "expected_latest_closed": (now - timedelta(days=1)).isoformat(),
                "symbols": [{"inst_id": symbol, "status": "passed"}],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(system_check, "configured_symbols", lambda: [symbol])

    results = system_check.run_runtime(
        status_path,
        mode="observation",
        max_age_seconds=120,
        max_pending=100,
    )
    by_name = {item.name: item for item in results}

    assert by_name["dashboard_5m_backfill_fresh"].ok is False
    assert by_name["dashboard_5m_backfill_fresh"].blocking is False
    assert by_name["dashboard_5m_backfill_complete"].ok is True
    assert by_name["dashboard_5m_symbol_coverage"].ok is True


def test_gui_closed_backfill_retries_transient_symbol_failures(monkeypatch) -> None:
    import gui
    from okx_signal_system.data import closed_backfill

    captured = {}
    updates = []

    class FakeService:
        def __init__(self, symbols, **kwargs):
            captured["symbols"] = list(symbols)
            captured.update(kwargs)

        def run_once(self):
            row = SimpleNamespace(
                status="passed",
                inst_id="BTC-USDT-SWAP",
                missing_closed_bars=0,
                added_rows=0,
            )
            return SimpleNamespace(
                all_complete=True,
                symbols_checked=1,
                expected_latest_closed="2026-07-01T04:30:00+00:00",
                symbols=[row],
                write_failures=0,
            )

    monkeypatch.setattr(closed_backfill, "ClosedCandleBackfillService", FakeService)
    dummy = SimpleNamespace(
        _watched_symbols=["BTC-USDT-SWAP"],
        api=SimpleNamespace(timeframe=SimpleNamespace(key="15m"), dataset="okx_15m"),
        _update_runtime_module_status=lambda *args, **kwargs: updates.append((args, kwargs)),
    )

    status = gui.OKXSignalGUI._run_closed_backfill_once(dummy)

    assert status.all_complete
    assert captured["max_symbol_attempts"] == 3
    assert captured["retry_delay_seconds"] == 1.0
    assert updates[0][0] == ("closed_kline_backfill", "healthy")


def test_gui_dashboard_5m_backfill_uses_latest_window_mode(monkeypatch) -> None:
    import gui
    from okx_signal_system.data import closed_backfill

    captured = {}
    updates = []

    class FakeService:
        def __init__(self, symbols, **kwargs):
            captured["symbols"] = list(symbols)
            captured.update(kwargs)

        def run_once(self):
            row = SimpleNamespace(
                status="passed",
                inst_id="BTC-USDT-SWAP",
                missing_closed_bars=0,
                latest_window_rebuilt=True,
            )
            return SimpleNamespace(
                all_complete=True,
                symbols_checked=1,
                expected_latest_closed="2026-06-28T13:00:00+00:00",
                symbols=[row],
                write_failures=0,
            )

    monkeypatch.setattr(closed_backfill, "ClosedCandleBackfillService", FakeService)
    dummy = SimpleNamespace(
        _watched_symbols=["BTC-USDT-SWAP"],
        _update_runtime_module_status=lambda *args, **kwargs: updates.append((args, kwargs)),
    )

    status = gui.OKXSignalGUI._run_dashboard_5m_backfill_once(dummy)

    assert status.all_complete
    assert captured["timeframe"] == "5m"
    assert captured["dataset"] == "okx_5m_extended"
    assert captured["fetch_limit"] == 300
    assert captured["replace_with_latest_window"] is True
    assert captured["max_symbol_attempts"] == 3
    assert captured["retry_delay_seconds"] == 1.0
    assert updates[0][0] == ("dashboard_5m_backfill", "healthy")
    assert "asyncio.create_task(self._dashboard_5m_backfill_loop())" in inspect.getsource(gui.OKXSignalGUI)


def test_dashboard_launch_command_prefers_local_next_cli(tmp_path, monkeypatch) -> None:
    import gui

    dashboard_dir = tmp_path / "dashboard"
    next_cli = dashboard_dir / "node_modules" / "next" / "dist" / "bin" / "next"
    next_cli.parent.mkdir(parents=True)
    next_cli.write_text("", encoding="utf-8")
    monkeypatch.setattr(gui.sys, "platform", "win32")
    monkeypatch.setattr(gui.shutil, "which", lambda name: "C:/node/node.exe" if name == "node.exe" else None)

    assert gui._dashboard_launch_command(dashboard_dir) == [
        "C:/node/node.exe",
        str(next_cli),
        "dev",
        "--hostname",
        "127.0.0.1",
        "--port",
        "3001",
    ]


def test_dashboard_launch_command_falls_back_to_npm(tmp_path, monkeypatch) -> None:
    import gui

    monkeypatch.setattr(gui.sys, "platform", "win32")
    monkeypatch.setattr(
        gui.shutil,
        "which",
        lambda name: "C:/node/npm.cmd" if name == "npm.cmd" else None,
    )

    assert gui._dashboard_launch_command(tmp_path) == ["C:/node/npm.cmd", "run", "dev:local"]


def test_gui_runtime_dependencies_import() -> None:
    import gui  # noqa: F401
    import okx_signal_system.ml.pattern_recognition  # noqa: F401
    import okx_signal_system.ml.rolling_backtest  # noqa: F401

    assert "auto_start" in inspect.signature(gui.start_gui).parameters
    assert gui.OKXSignalGUI._breakout_gap_pct(None) is None
    assert gui.APP_VERSION == f"v{okx_signal_system.__version__}"
    assert gui._format_beijing_time(pd.Timestamp("2026-06-16T00:00:00Z")) == "2026-06-16 08:00"
    assert gui._symbol_panel_title(21) == "监控币种（21个，列表可滚动）"
    assert gui._symbol_panel_title(1, degraded=True) == "监控币种（配置加载失败，已降级为1个）"
    assert gui._symbol_list_height(21) == 10
    assert gui._symbol_list_height(3) == 4


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


def test_manual_confirmation_auto_stop_trigger_only_reports_signal_outcome(tmp_path) -> None:
    monitor = AutoStopMonitor(auto_close_enabled=False, store=PositionRecordStore(tmp_path))
    results = []
    monitor.set_on_close_callback(results.append)
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

    monitor._handle_trigger(record, 94.5, "stop_loss")

    assert len(results) == 1
    assert results[0].exit_reason == "stop_loss"


def test_realtime_runtime_api_is_signal_only(monkeypatch) -> None:
    from okx_signal_system.exchange import realtime

    def _fail_lookup(_dataset: str):  # pragma: no cover - defensive
        raise AssertionError("history lookup should be lazy")

    monkeypatch.setattr(realtime, "find_lightweight_history", _fail_lookup)
    api = realtime.OKXRealtimeAPI({})

    assert not hasattr(api, "place_order")
    assert not hasattr(api, "cancel_order")
    assert not hasattr(api, "get_positions")
    assert not hasattr(api, "get_account_balance")
    assert hasattr(api, "get_market_data")
    assert hasattr(api, "get_candles")


def test_realtime_runtime_does_not_import_execution_functions() -> None:
    source = (Path(__file__).parents[1] / "src" / "okx_signal_system" / "exchange" / "realtime.py").read_text(
        encoding="utf-8"
    )

    assert "close_position" not in source
    assert "get_account_positions" not in source
    assert "get_account_balance" not in source
    assert "from okx_signal_system.exchange.okx import place_order" not in source


def test_live_signal_monitor_auto_close_disabled_by_default(monkeypatch) -> None:
    from okx_signal_system.exchange import realtime

    class FakeApi:
        def __init__(self):
            self.config = {"execution": {"auto_close_enabled": True, "live_order_enabled": True}}
            self.timeframe = type("Timeframe", (), {"hours": 0.25})()

    monitor = realtime.LiveSignalMonitor(FakeApi())
    position = realtime.Position(
        inst_id="BTC-USDT-SWAP",
        side="long",
        size=1.0,
        entry_price=100.0,
        unrealized_pnl=0.0,
        margin=20.0,
        leverage=5.0,
        liquidation_price=None,
    )
    market = realtime.MarketData(
        inst_id="BTC-USDT-SWAP",
        last_price=94.0,
        bid_price=94.0,
        ask_price=94.1,
        volume_24h=1000.0,
        timestamp=pd.Timestamp("2026-01-01T00:00:00Z").to_pydatetime(),
        open=95.0,
        high=95.0,
        low=94.0,
        close=94.0,
        volume=100.0,
    )

    asyncio.run(monitor._check_exit_conditions(position, market))
    monitor._position_entries[position.inst_id] = (
        pd.Timestamp("2025-12-31T00:00:00Z"),
        StrategyParams(max_hold_bars=1),
    )
    asyncio.run(monitor._check_hold_timeout(position, market))

    assert monitor._auto_close_enabled is False


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


def test_vote_gate_uses_support_ratio_not_unanimity() -> None:
    assert min_vote_approval_rate({"strategy": {"min_vote_approval_rate": 0.5}}) == 0.5
    assert vote_gate_passed("long", "long", 0.50, 0.40)
    assert not vote_gate_passed("long", "long", 0.25, 0.40)
    assert not vote_gate_passed("short", "long", 0.80, 0.40)


def test_publish_tiered_candidates_uses_scan_service_selection() -> None:
    from okx_signal_system.exchange.realtime import LiveSignalMonitor
    from okx_signal_system.risk.model import RiskDecision
    from okx_signal_system.signal_quality import SignalCandidate, TieredSelection
    from okx_signal_system.strategy.trend_breakout import TradeSignal

    class Store:
        def __init__(self, *, existing: bool = False):
            self.existing = existing
            self.marked = []

        def has(self, _key):
            return self.existing

        def mark(self, key, metadata=None):
            self.marked.append((key, metadata))
            return True

    def candidate(symbol: str, score: float, *, tier: str, rank: int) -> SignalCandidate:
        signal = TradeSignal(
            ts=pd.Timestamp("2026-01-01T00:00:00Z"),
            inst_id=symbol,
            side="long",
            entry_ref=100.0,
            stop_loss=98.0,
            take_profit=107.0,
            max_hold_bars=12,
            reason_codes=("TEST",),
            signal_score=score,
            risk_reward_ratio=3.5,
        )
        decision = RiskDecision(
            accepted=True,
            reason=None,
            leverage_cap=3.0,
            qty=1.0,
            risk_amount=100.0,
            leverage_used=3.0,
            signal_score=score,
            risk_reward_ratio=3.5,
        )
        return SignalCandidate(
            signal=signal,
            decision=decision,
            notify_key=f"{symbol}:{score}",
            payload={"signal": {"signal_score": score}},
            health_item={"symbol": symbol, "would_push": True},
            rank_score=score,
            raw_score=score,
            tier=tier,
            rank=rank,
            correlation_group=f"group:{symbol}",
        )

    low_score_a = candidate("LOW-USDT-SWAP", 6.0, tier="A", rank=1)
    high_score_b = candidate("HIGH-USDT-SWAP", 9.0, tier="B", rank=2)
    selection = TieredSelection(
        ranked=[low_score_a, high_score_b],
        tier_a=[low_score_a],
        tier_b=[high_score_b],
        tier_c=[],
    )
    pushed = []
    recorded = []
    outbox = []

    monitor = LiveSignalMonitor.__new__(LiveSignalMonitor)
    monitor.api = type(
        "Api",
        (),
        {
            "timeframe": type("Timeframe", (), {"key": "15m"})(),
            "trend_timeframe": type("Timeframe", (), {"key": "1h"})(),
        },
    )()
    callback_candidates = []

    def signal_callback(signal, _decision, candidate):
        pushed.append(signal.inst_id)
        callback_candidates.append(candidate)
        return True

    monitor.signal_callback = signal_callback
    monitor._lifecycle_store = type(
        "LifecycleStore",
        (),
        {
            "enqueue_notification": lambda self, key, **metadata: outbox.append(("pending", key, metadata)),
        },
    )()
    monitor._last_ready_signal = None

    asyncio.run(monitor._publish_tiered_candidates(selection))

    assert pushed == ["LOW-USDT-SWAP"]
    assert callback_candidates == [low_score_a]
    assert callback_candidates[0].payload is low_score_a.payload
    assert callback_candidates[0].payload["rank"] == 1
    assert callback_candidates[0].payload["total_formal_candidates"] == 2
    assert callback_candidates[0].health_item["rank"] == 1
    assert callback_candidates[0].health_item["total_formal_candidates"] == 2
    assert recorded == []
    assert outbox == [
        (
            "pending",
            "LOW-USDT-SWAP:6.0",
            {
                "signal_id": None,
                "event_type": "A_TIER_SIGNAL",
                "payload": low_score_a.payload,
            },
        )
    ]
    assert low_score_a.health_item["tier"] == "A"
    assert high_score_b.health_item["tier"] == "B"


def test_publish_tiered_candidates_keeps_legacy_two_arg_callback() -> None:
    from okx_signal_system.exchange.realtime import LiveSignalMonitor
    from okx_signal_system.risk.model import RiskDecision
    from okx_signal_system.signal_quality import SignalCandidate, TieredSelection
    from okx_signal_system.strategy.trend_breakout import TradeSignal

    signal = TradeSignal(
        ts=pd.Timestamp("2026-01-01T00:00:00Z"),
        inst_id="BTC-USDT-SWAP",
        side="long",
        entry_ref=100.0,
        stop_loss=98.0,
        take_profit=107.0,
        max_hold_bars=12,
        reason_codes=("TEST",),
        signal_score=8.0,
        risk_reward_ratio=3.5,
    )
    decision = RiskDecision(
        accepted=True,
        reason=None,
        leverage_cap=3.0,
        qty=1.0,
        risk_amount=100.0,
        leverage_used=3.0,
        signal_score=8.0,
        risk_reward_ratio=3.5,
    )
    candidate = SignalCandidate(
        signal=signal,
        decision=decision,
        notify_key="BTC-USDT-SWAP:8.0",
        payload={"signal": {"signal_score": 8.0}},
        health_item={"symbol": "BTC-USDT-SWAP", "would_push": True},
        rank_score=8.0,
        raw_score=8.0,
        tier="A",
        rank=1,
        correlation_group="solo:BTC-USDT-SWAP",
    )
    pushed = []
    monitor = LiveSignalMonitor.__new__(LiveSignalMonitor)
    monitor.api = type(
        "Api",
        (),
        {
            "timeframe": type("Timeframe", (), {"key": "15m"})(),
            "trend_timeframe": type("Timeframe", (), {"key": "1h"})(),
        },
    )()
    monitor.signal_callback = lambda signal, _decision: pushed.append(signal.inst_id) or True
    monitor._shadow_ledger = type("ShadowLedger", (), {"record_signal": lambda self, signal, _decision: None})()
    monitor._lifecycle_store = type(
        "LifecycleStore",
        (),
        {
            "enqueue_notification": lambda self, key, **metadata: None,
        },
    )()
    monitor._last_ready_signal = None

    asyncio.run(
        monitor._publish_tiered_candidates(
            TieredSelection(ranked=[candidate], tier_a=[candidate], tier_b=[], tier_c=[])
        )
    )

    assert pushed == ["BTC-USDT-SWAP"]
