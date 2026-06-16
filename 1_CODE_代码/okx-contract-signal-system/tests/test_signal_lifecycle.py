from __future__ import annotations

import json
import sqlite3

import pandas as pd

from okx_signal_system.signal_quality.lifecycle import SignalLifecycleStore, lifecycle_payload
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


def test_lifecycle_target_reached_after_confirmation(tmp_path) -> None:
    store = SignalLifecycleStore(tmp_path / "lifecycle.json")
    signal = _signal(take_profit=115.0)
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
            {"ts": pd.Timestamp("2026-01-01T00:30:00Z"), "close": 115.0, "is_closed": True},
        ]
    )
    assert store.update_symbol("BTC-USDT-SWAP", second) == 1
    record = store.get("sig-1")
    assert record.status == "TARGET_REACHED"
    payload = lifecycle_payload(record)
    assert payload["state"] == "TARGET_REACHED"
    assert payload["lifecycle_event"] == {
        "type": "TARGET_REACHED",
        "at": "2026-01-01T00:30:00+00:00",
    }
    assert payload["target_price"] == 115.0
    assert payload["take_profit"] == 115.0
    assert payload["target_reached_at"] == "2026-01-01T00:30:00+00:00"
    assert payload["last_updated_at"]

    summary = store.summary()
    assert summary["triggered"] == 0
    assert summary["target_reached"] == 1
    assert summary["active"] == 0
    assert summary["terminal"] == 1
    assert summary["latest_event_type"] == "TARGET_REACHED"
    assert summary["latest_event_at"] == "2026-01-01T00:30:00+00:00"


def test_lifecycle_stop_reached_after_confirmation(tmp_path) -> None:
    store = SignalLifecycleStore(tmp_path / "lifecycle.json")
    signal = _signal(stop_loss=95.0, take_profit=115.0)
    store.record_signal(signal, signal_id="sig-2")

    frame = _frame(
        [
            {"ts": pd.Timestamp("2026-01-01T00:15:00Z"), "close": 101.0, "is_closed": True},
            {"ts": pd.Timestamp("2026-01-01T00:30:00Z"), "close": 94.5, "is_closed": True},
        ]
    )
    assert store.update_symbol("BTC-USDT-SWAP", frame) == 1
    record = store.get("sig-2")
    assert record.status == "STOP_REACHED"
    assert record.stop_reached_at is not None
    payload = lifecycle_payload(record)
    assert payload["lifecycle_event"]["type"] == "STOP_REACHED"
    assert payload["stop_reached_at"] == "2026-01-01T00:30:00+00:00"

    summary = store.summary()
    assert summary["stop_reached"] == 1
    assert summary["terminal"] == 1
    assert summary["latest_event_type"] == "STOP_REACHED"


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


def test_lifecycle_timeout_result_after_confirmation(tmp_path) -> None:
    store = SignalLifecycleStore(tmp_path / "lifecycle.json")
    signal = _signal(max_hold_bars=2, take_profit=120.0)
    store.record_signal(signal, signal_id="sig-timeout-result")

    frame = _frame(
        [
            {"ts": pd.Timestamp("2026-01-01T00:15:00Z"), "close": 101.0, "is_closed": True},
            {"ts": pd.Timestamp("2026-01-01T00:30:00Z"), "close": 102.0, "is_closed": True},
        ]
    )
    assert store.update_symbol("BTC-USDT-SWAP", frame) == 1
    record = store.get("sig-timeout-result")
    assert record.status == "TIMEOUT_RESULT"
    assert record.timeout_result_at == "2026-01-01T00:30:00+00:00"
    payload = lifecycle_payload(record)
    assert payload["lifecycle_event"] == {
        "type": "TIMEOUT_RESULT",
        "at": "2026-01-01T00:30:00+00:00",
    }
    summary = store.summary()
    assert summary["timeout_result"] == 1
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
    store.record_signal(_signal(take_profit=115.0), signal_id="sig-sqlite")
    store.update_symbol(
        "BTC-USDT-SWAP",
        _frame(
            [
                {"ts": pd.Timestamp("2026-01-01T00:15:00Z"), "close": 101.0, "is_closed": True},
                {"ts": pd.Timestamp("2026-01-01T00:30:00Z"), "close": 115.0, "is_closed": True},
            ]
        ),
    )

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
            "SELECT outbox_id, status, payload_json FROM notification_outbox WHERE signal_id = ? ORDER BY created_at",
            ("sig-sqlite",),
        ).fetchall()

    assert {"lifecycle_records", "lifecycle_events", "notification_outbox"}.issubset(tables)
    assert events == [("TRIGGERED", "TRIGGERED"), ("CONFIRMED", "CONFIRMED"), ("TARGET_REACHED", "TARGET_REACHED")]
    assert [row[0] for row in outbox] == [
        "sig-sqlite:TRIGGERED:2026-01-01T00:00:00+00:00",
        "sig-sqlite:CONFIRMED:2026-01-01T00:15:00+00:00",
        "sig-sqlite:TARGET_REACHED:2026-01-01T00:30:00+00:00",
    ]
    assert [row[1] for row in outbox] == ["PENDING", "PENDING", "PENDING"]
    assert json.loads(outbox[-1][2])["state"] == "TARGET_REACHED"
    assert store.summary()["outbox"]["pending"] == 3
    pending = store.pending_notifications()
    assert [item["outbox_id"] for item in pending] == [row[0] for row in outbox]
    assert len({item["outbox_id"] for item in pending}) == len(pending)


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
                    "take_profit": 115.0,
                    "max_hold_bars": 3,
                    "status": "TARGET_REACHED",
                    "target_reached_at": "2026-01-01T00:30:00+00:00",
                    "last_event_type": "TARGET_REACHED",
                    "last_event_at": "2026-01-01T00:30:00+00:00",
                    "created_at": "2026-01-01T00:00:00+00:00",
                    "updated_at": "2026-01-01T00:00:00+00:00",
                }
            ]
        ),
        encoding="utf-8",
    )

    store = SignalLifecycleStore(tmp_path / "lifecycle.sqlite3")

    assert store.get("legacy-sig") is not None
    assert store.get("legacy-sig").take_profit == 115.0
    with sqlite3.connect(tmp_path / "lifecycle.sqlite3") as conn:
        assert conn.execute("SELECT COUNT(*) FROM lifecycle_records").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM lifecycle_events").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM notification_outbox").fetchone()[0] == 1


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
