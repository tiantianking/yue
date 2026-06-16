from __future__ import annotations

import json
import sqlite3

import pandas as pd

from okx_signal_system.signal_quality import SignalLifecycleStore, lifecycle_payload
from okx_signal_system.strategy.trend_breakout import TradeSignal


def _signal(*, side: str = "long", ts: str = "2026-01-01T00:00:00Z", entry_ref: float = 100.0, stop_loss: float = 95.0, take_profit: float = 115.0, max_hold_bars: int = 3) -> TradeSignal:
    return TradeSignal(
        ts=pd.Timestamp(ts),
        inst_id="BTC-USDT-SWAP",
        side=side,
        entry_ref=entry_ref,
        stop_loss=stop_loss,
        take_profit=take_profit,
        max_hold_bars=max_hold_bars,
        reason_codes=("TEST",),
        signal_score=8.0,
        risk_reward_ratio=3.0,
    )


def _frame(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def test_lifecycle_confirmed_after_later_closed_candle(tmp_path) -> None:
    store = SignalLifecycleStore(tmp_path / "lifecycle.json")
    signal = _signal()
    record = store.record_signal(signal, signal_id="sig-1")
    assert record is not None
    assert record.status == "TRIGGERED"

    first = _frame(
        [
            {"ts": pd.Timestamp("2026-01-01T00:15:00Z"), "close": 99.0, "is_closed": True},
        ]
    )
    assert store.update_symbol("BTC-USDT-SWAP", first) == 1
    assert store.get("sig-1").status == "TRIGGERED"

    second = _frame(
        [
            {"ts": pd.Timestamp("2026-01-01T00:15:00Z"), "close": 99.0, "is_closed": True},
            {"ts": pd.Timestamp("2026-01-01T00:30:00Z"), "close": 101.0, "is_closed": True},
        ]
    )
    assert store.update_symbol("BTC-USDT-SWAP", second) == 1
    record = store.get("sig-1")
    assert record.status == "CONFIRMED"
    payload = lifecycle_payload(record)
    assert payload["state"] == "CONFIRMED"
    assert payload["lifecycle_event"] == {
        "type": "CONFIRMED",
        "at": "2026-01-01T00:30:00+00:00",
    }
    assert payload["last_updated_at"]

    summary = store.summary()
    assert summary["triggered"] == 0
    assert summary["confirmed"] == 1
    assert summary["active"] == 1
    assert summary["terminal"] == 0
    assert summary["latest_event_type"] == "CONFIRMED"
    assert summary["latest_event_at"] == "2026-01-01T00:30:00+00:00"


def test_lifecycle_invalidates_on_immediate_reversal(tmp_path) -> None:
    store = SignalLifecycleStore(tmp_path / "lifecycle.json")
    signal = _signal(stop_loss=95.0)
    store.record_signal(signal, signal_id="sig-2")

    frame = _frame(
        [
            {"ts": pd.Timestamp("2026-01-01T00:15:00Z"), "close": 94.5, "is_closed": True},
        ]
    )
    assert store.update_symbol("BTC-USDT-SWAP", frame) == 1
    record = store.get("sig-2")
    assert record.status == "INVALIDATED"
    assert record.invalidated_at is not None
    payload = lifecycle_payload(record)
    assert payload["lifecycle_event"]["type"] == "INVALIDATED"
    assert payload["invalidated_at"] == "2026-01-01T00:15:00+00:00"

    summary = store.summary()
    assert summary["invalidated"] == 1
    assert summary["terminal"] == 1
    assert summary["latest_event_type"] == "INVALIDATED"


def test_lifecycle_ignores_unclosed_reversal(tmp_path) -> None:
    store = SignalLifecycleStore(tmp_path / "lifecycle.json")
    signal = _signal(stop_loss=95.0)
    store.record_signal(signal, signal_id="sig-unclosed")

    frame = _frame(
        [
            {"ts": pd.Timestamp("2026-01-01T00:15:00Z"), "close": 94.5, "is_closed": False},
        ]
    )
    assert store.update_symbol("BTC-USDT-SWAP", frame) == 0
    assert store.get("sig-unclosed").status == "TRIGGERED"


def test_lifecycle_expires_after_hold_limit(tmp_path) -> None:
    store = SignalLifecycleStore(tmp_path / "lifecycle.json")
    signal = _signal(max_hold_bars=2)
    store.record_signal(signal, signal_id="sig-3")

    frame = _frame(
        [
            {"ts": pd.Timestamp("2026-01-01T00:15:00Z"), "close": 99.0, "is_closed": True},
            {"ts": pd.Timestamp("2026-01-01T00:30:00Z"), "close": 99.5, "is_closed": True},
        ]
    )
    assert store.update_symbol("BTC-USDT-SWAP", frame) == 1
    record = store.get("sig-3")
    assert record.status == "EXPIRED"
    assert record.expired_at is not None
    payload = lifecycle_payload(record)
    assert payload["lifecycle_event"] == {
        "type": "EXPIRED",
        "at": "2026-01-01T00:30:00+00:00",
    }
    assert payload["expired_at"] == "2026-01-01T00:30:00+00:00"

    summary = store.summary()
    assert summary["expired"] == 1
    assert summary["active"] == 0
    assert summary["terminal"] == 1


def test_lifecycle_persists_records(tmp_path) -> None:
    path = tmp_path / "lifecycle.json"
    store = SignalLifecycleStore(path)
    store.record_signal(_signal(side="short", stop_loss=105.0, entry_ref=100.0), signal_id="sig-4")
    reloaded = SignalLifecycleStore(path)
    record = reloaded.get("sig-4")
    assert record is not None
    assert record.invalidation_price == 105.0
    assert record.status == "TRIGGERED"
    assert lifecycle_payload(record)["lifecycle_event"] == {
        "type": "TRIGGERED",
        "at": "2026-01-01T00:00:00+00:00",
    }
    json.dumps(lifecycle_payload(record))
    json.dumps(reloaded.summary())


def test_lifecycle_record_signal_is_idempotent_and_persistence_is_stable(tmp_path) -> None:
    path = tmp_path / "lifecycle.sqlite3"
    store = SignalLifecycleStore(path)
    first = store.record_signal(_signal(), signal_id="sig-stable")
    second = store.record_signal(_signal(), signal_id="sig-stable")

    assert first is second
    assert len(store.records) == 1

    with sqlite3.connect(path) as conn:
        first_payload = conn.execute("SELECT COUNT(*) FROM lifecycle_records").fetchone()[0]
    reloaded = SignalLifecycleStore(path)
    assert len(reloaded.records) == 1
    reloaded.record_signal(_signal(), signal_id="sig-stable")
    with sqlite3.connect(path) as conn:
        second_payload = conn.execute("SELECT COUNT(*) FROM lifecycle_records").fetchone()[0]

    assert second_payload == first_payload


def test_lifecycle_sqlite_schema_records_events_and_outbox(tmp_path) -> None:
    path = tmp_path / "lifecycle.sqlite3"
    store = SignalLifecycleStore(path)
    store.record_signal(_signal(), signal_id="sig-sqlite")
    store.update_symbol(
        "BTC-USDT-SWAP",
        _frame(
            [
                {"ts": pd.Timestamp("2026-01-01T00:15:00Z"), "close": 101.0, "is_closed": True},
            ]
        ),
    )
    store.enqueue_notification(
        "outbox-1",
        signal_id="sig-sqlite",
        event_type="A_TIER_SIGNAL",
        payload={"symbol": "BTC-USDT-SWAP"},
    )
    assert store.pending_notifications()[0]["outbox_id"] == "outbox-1"
    store.mark_notification_sent("outbox-1")

    with sqlite3.connect(path) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        events = conn.execute(
            "SELECT event_type, status FROM lifecycle_events WHERE signal_id = ? ORDER BY event_id",
            ("sig-sqlite",),
        ).fetchall()
        outbox = conn.execute(
            "SELECT status, sent_at FROM notification_outbox WHERE outbox_id = ?",
            ("outbox-1",),
        ).fetchone()

    assert {"lifecycle_records", "lifecycle_events", "notification_outbox"}.issubset(tables)
    assert events == [("TRIGGERED", "TRIGGERED"), ("CONFIRMED", "CONFIRMED")]
    assert outbox[0] == "SENT"
    assert outbox[1]
    assert store.summary()["outbox"]["sent"] == 1


def test_lifecycle_migrates_legacy_json_to_sqlite(tmp_path) -> None:
    legacy_path = tmp_path / "lifecycle.json"
    legacy_path.write_text(
        json.dumps(
            [
                {
                    "signal_id": "legacy-sig",
                    "inst_id": "BTC-USDT-SWAP",
                    "side": "long",
                    "signal_time": "2026-01-01T00:00:00+00:00",
                    "entry_ref": 100.0,
                    "invalidation_price": 95.0,
                    "max_hold_bars": 3,
                    "status": "TRIGGERED",
                    "last_event_type": "TRIGGERED",
                    "last_event_at": "2026-01-01T00:00:00+00:00",
                    "created_at": "2026-01-01T00:00:00+00:00",
                    "updated_at": "2026-01-01T00:00:00+00:00",
                }
            ]
        ),
        encoding="utf-8",
    )

    store = SignalLifecycleStore(tmp_path / "lifecycle.sqlite3")

    assert store.get("legacy-sig") is not None
    with sqlite3.connect(tmp_path / "lifecycle.sqlite3") as conn:
        assert conn.execute("SELECT COUNT(*) FROM lifecycle_records").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM lifecycle_events").fetchone()[0] == 1


def test_gui_lifecycle_table_values_match_visible_columns(tmp_path) -> None:
    from gui import lifecycle_table_values

    store = SignalLifecycleStore(tmp_path / "lifecycle.json")
    record = store.record_signal(_signal(ts="2026-01-01T00:00:00Z"), signal_id="sig-gui")
    store.update_symbol(
        "BTC-USDT-SWAP",
        _frame(
            [
                {"ts": pd.Timestamp("2026-01-01T00:15:00Z"), "close": 99.0, "is_closed": True},
            ]
        ),
    )

    values = lifecycle_table_values(record)

    assert values == (
        "BTC-USDT-SWAP",
        "多",
        "100.00",
        "99.00",
        "95.00",
        "TRIGGERED",
        "1",
        "-",
    )
