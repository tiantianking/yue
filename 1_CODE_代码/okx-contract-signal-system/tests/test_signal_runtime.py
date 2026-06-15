from __future__ import annotations

import pandas as pd

from okx_signal_system.notify.signal_dedupe import SignalNotificationStore, signal_notification_key
from okx_signal_system.signal_runtime import (
    closed_bar_lag_minutes,
    latest_closed_signal,
    make_signal_id,
    parameter_hash,
    signal_is_stale,
)
from okx_signal_system.strategy.trend_breakout import StrategyParams, TradeSignal


def _accepted_signal(ts: pd.Timestamp, side: str = "long") -> TradeSignal:
    return TradeSignal(
        ts=ts,
        inst_id="BTC-USDT-SWAP",
        side=side,
        entry_ref=100.0,
        stop_loss=98.0,
        take_profit=107.0,
        max_hold_bars=12,
        reason_codes=("TEST",),
        signal_score=7.0,
        risk_reward_ratio=3.5,
    )


def test_latest_closed_signal_does_not_return_old_history_signal(monkeypatch) -> None:
    ts = pd.date_range("2026-01-01T00:00:00Z", periods=3, freq="15min")
    frame = pd.DataFrame({"ts": ts, "close": [100.0, 101.0, 102.0]})

    def fake_build_signal(row, *, inst_id, params, frame, idx):
        assert idx == len(frame) - 1
        return TradeSignal(
            ts=row["ts"],
            inst_id=inst_id,
            side="flat",
            entry_ref=None,
            stop_loss=None,
            take_profit=None,
            max_hold_bars=None,
            reason_codes=("NO_SIGNAL",),
            reject_reason="no_signal",
        )

    monkeypatch.setattr("okx_signal_system.signal_runtime.build_signal", fake_build_signal)

    assert latest_closed_signal(frame, inst_id="BTC-USDT-SWAP", params=StrategyParams()) is None


def test_latest_closed_signal_requires_signal_time_to_match_latest_row(monkeypatch) -> None:
    ts = pd.date_range("2026-01-01T00:00:00Z", periods=3, freq="15min")
    frame = pd.DataFrame({"ts": ts, "close": [100.0, 101.0, 102.0]})

    def fake_build_signal(row, *, inst_id, params, frame, idx):
        return _accepted_signal(ts[0])

    monkeypatch.setattr("okx_signal_system.signal_runtime.build_signal", fake_build_signal)

    assert latest_closed_signal(frame, inst_id="BTC-USDT-SWAP", params=StrategyParams()) is None


def test_signal_stale_uses_candle_close_time_not_start_time() -> None:
    candle = pd.Timestamp("2026-01-01T00:00:00Z")
    assert closed_bar_lag_minutes(candle, timeframe="15m", now="2026-01-01T00:18:00Z") == 3.0
    assert not signal_is_stale(candle, timeframe="15m", now="2026-01-01T00:34:00Z", max_lag_minutes=20)
    assert signal_is_stale(candle, timeframe="15m", now="2026-01-01T00:36:00Z", max_lag_minutes=20)


def test_signal_id_includes_strategy_version_and_parameter_hash() -> None:
    params = StrategyParams(fast_ema=120)
    candle = "2026-01-01T00:00:00Z"
    first = make_signal_id("BTC-USDT-SWAP", candle, "long", "3.28.0", parameter_hash(params))
    changed_params = make_signal_id(
        "BTC-USDT-SWAP",
        candle,
        "long",
        "3.28.0",
        parameter_hash(StrategyParams(fast_ema=96)),
    )
    changed_version = make_signal_id("BTC-USDT-SWAP", candle, "long", "3.29.0", parameter_hash(params))

    assert first != changed_params
    assert first != changed_version


def test_sqlite_signal_notification_store_persists_across_restart(tmp_path) -> None:
    path = tmp_path / "pushed_signals.sqlite3"
    signal = _accepted_signal(pd.Timestamp("2026-01-01T00:00:00Z"))
    key = signal_notification_key(signal, params=StrategyParams())

    store = SignalNotificationStore(path)
    assert store.mark(key, {"symbol": signal.inst_id, "side": signal.side, "kline_time": signal.ts.isoformat()})
    assert store.has(key)

    reloaded = SignalNotificationStore(path)
    assert reloaded.has(key)
    assert not reloaded.mark(key, {"symbol": signal.inst_id, "side": signal.side, "kline_time": signal.ts.isoformat()})
