from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace

import pandas as pd
import pytest

import okx_signal_system.signal_quality.lifecycle as lifecycle_module
from okx_signal_system.signal_quality.labeler import label_signal
from okx_signal_system.signal_quality.lifecycle import LifecycleOutboxWorker, SignalLifecycleStore, lifecycle_payload
from okx_signal_system.signal_quality.outcome import SIGNAL_OUTCOME_POLICY, SignalOutcomeSimulator
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
    assert summary["active"] == 1
    assert summary["terminal"] == 1
    assert summary["latest_event_type"] == "TARGET_REACHED"
    assert summary["latest_event_at"] == "2026-01-01T00:30:00+00:00"


def test_lifecycle_target_uses_high_when_close_stays_below_target(tmp_path) -> None:
    store = SignalLifecycleStore(tmp_path / "lifecycle.json")
    signal = _signal(take_profit=115.0, max_hold_bars=3)
    store.record_signal(signal, signal_id="sig-high-target")

    frame = _frame(
        [
            {
                "ts": pd.Timestamp("2026-01-01T00:15:00Z"),
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 101.0,
                "is_closed": True,
            },
            {
                "ts": pd.Timestamp("2026-01-01T00:30:00Z"),
                "open": 101.0,
                "high": 116.0,
                "low": 100.5,
                "close": 112.0,
                "is_closed": True,
            },
        ]
    )

    assert store.update_symbol("BTC-USDT-SWAP", frame) == 1
    record = store.get("sig-high-target")
    assert record.status == "TARGET_REACHED"
    assert record.target_reached_at == "2026-01-01T00:30:00+00:00"


def test_lifecycle_result_matches_labeler_when_tp_hits_before_confirmation(tmp_path) -> None:
    store = SignalLifecycleStore(tmp_path / "lifecycle.json")
    signal = _signal(entry_ref=100.0, stop_loss=95.0, take_profit=110.0, max_hold_bars=3)
    store.record_signal(signal, signal_id="sig-preconfirm-tp")
    frame = _frame(
        [
            {
                "ts": pd.Timestamp("2026-01-01T00:15:00Z"),
                "open": 100.0,
                "high": 111.0,
                "low": 99.0,
                "close": 99.5,
                "is_closed": True,
            },
        ]
    )

    expected = SignalOutcomeSimulator().simulate_signal(
        signal,
        frame,
        policy=SIGNAL_OUTCOME_POLICY,
        require_complete_timeout=True,
    )
    label = label_signal(signal, frame)
    assert expected is not None
    assert label is not None
    assert expected.outcome == "TP"
    assert label.outcome == expected.outcome

    assert store.update_symbol("BTC-USDT-SWAP", frame) == 1
    record = store.get("sig-preconfirm-tp")
    assert record.status == "TARGET_REACHED"
    assert record.confirmed_at is None
    assert record.setup_state == "TRIGGERED"
    assert record.outcome_state == "TARGET_REACHED"
    assert record.target_reached_at == pd.Timestamp(expected.exit_time).isoformat()


def test_lifecycle_maps_outcome_simulator_result_without_price_recalculation(tmp_path, monkeypatch) -> None:
    store = SignalLifecycleStore(tmp_path / "lifecycle.json")
    signal = _signal(entry_ref=100.0, stop_loss=95.0, take_profit=115.0, max_hold_bars=3)
    store.record_signal(signal, signal_id="sig-simulator-owned")
    calls = []

    class FakeOutcomeSimulator:
        def simulate_signal(self, signal_record, *, frame, closed_only, after_signal_time, policy, require_complete_timeout):
            calls.append(
                {
                    "signal_id": signal_record.signal_id,
                    "closed_only": closed_only,
                    "after_signal_time": after_signal_time,
                    "require_complete_timeout": require_complete_timeout,
                    "rows": len(frame),
                }
            )
            return SimpleNamespace(outcome="TP", exit_time=pd.Timestamp("2026-01-01T00:15:00Z"))

    monkeypatch.setattr(lifecycle_module, "_OUTCOME_SIMULATOR", FakeOutcomeSimulator())

    frame = _frame(
        [
            {
                "ts": pd.Timestamp("2026-01-01T00:15:00Z"),
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 99.5,
                "is_closed": True,
            },
        ]
    )

    assert store.update_symbol("BTC-USDT-SWAP", frame) == 1
    record = store.get("sig-simulator-owned")
    assert calls == [
        {
            "signal_id": "sig-simulator-owned",
            "closed_only": True,
            "after_signal_time": False,
            "require_complete_timeout": True,
            "rows": 1,
        }
    ]
    assert record.setup_state == "TRIGGERED"
    assert record.outcome_state == "TARGET_REACHED"
    assert record.status == "TARGET_REACHED"
    assert record.target_reached_at == "2026-01-01T00:15:00+00:00"


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


def test_confirmed_signal_without_take_profit_stops_on_later_closed_bar(tmp_path) -> None:
    store = SignalLifecycleStore(tmp_path / "lifecycle.sqlite3")
    signal = _signal(stop_loss=95.0, take_profit=None, max_hold_bars=4)
    store.record_signal(signal, signal_id="sig-no-tp-stop")

    frame = _frame(
        [
            {"ts": pd.Timestamp("2026-01-01T00:15:00Z"), "open": 100.0, "high": 102.0, "low": 99.0, "close": 101.0, "is_closed": True},
            {"ts": pd.Timestamp("2026-01-01T00:30:00Z"), "open": 101.0, "high": 101.5, "low": 94.0, "close": 96.0, "is_closed": True},
        ]
    )

    assert store.update_symbol("BTC-USDT-SWAP", frame) == 1
    record = store.get("sig-no-tp-stop")
    assert record.setup_state == "CONFIRMED"
    assert record.outcome_state == "STOP_REACHED"
    assert record.status == "STOP_REACHED"
    assert record.stop_reached_at == "2026-01-01T00:30:00+00:00"


def test_confirmed_signal_without_take_profit_times_out(tmp_path) -> None:
    store = SignalLifecycleStore(tmp_path / "lifecycle.sqlite3")
    signal = _signal(stop_loss=95.0, take_profit=None, max_hold_bars=2)
    store.record_signal(signal, signal_id="sig-no-tp-timeout")

    frame = _frame(
        [
            {"ts": pd.Timestamp("2026-01-01T00:15:00Z"), "open": 100.0, "high": 102.0, "low": 99.0, "close": 101.0, "is_closed": True},
            {"ts": pd.Timestamp("2026-01-01T00:30:00Z"), "open": 101.0, "high": 103.0, "low": 100.0, "close": 102.0, "is_closed": True},
        ]
    )

    assert store.update_symbol("BTC-USDT-SWAP", frame) == 1
    record = store.get("sig-no-tp-timeout")
    assert record.setup_state == "CONFIRMED"
    assert record.outcome_state == "TIMEOUT_RESULT"
    assert record.status == "TIMEOUT_RESULT"
    assert record.timeout_result_at == "2026-01-01T00:30:00+00:00"


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
    signal = _signal(max_hold_bars=2, take_profit=None)
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
    assert record.setup_state == "CONFIRMED"
    assert record.outcome_state == "TIMEOUT_RESULT"
    assert record.timeout_result_at == "2026-01-01T00:30:00+00:00"
    payload = lifecycle_payload(record)
    assert payload["lifecycle_event"] == {
        "type": "TIMEOUT_RESULT",
        "at": "2026-01-01T00:30:00+00:00",
    }
    summary = store.summary()
    assert summary["timeout_result"] == 1
    assert summary["terminal"] == 1


def test_lifecycle_does_not_timeout_result_on_incomplete_tail(tmp_path) -> None:
    store = SignalLifecycleStore(tmp_path / "lifecycle.json")
    signal = _signal(max_hold_bars=3, take_profit=120.0)
    store.record_signal(signal, signal_id="sig-incomplete-timeout")
    frame = _frame(
        [
            {"ts": pd.Timestamp("2026-01-01T00:15:00Z"), "close": 101.0, "is_closed": True},
            {"ts": pd.Timestamp("2026-01-01T00:30:00Z"), "close": 102.0, "is_closed": True},
        ]
    )

    assert label_signal(signal, frame) is None
    assert store.update_symbol("BTC-USDT-SWAP", frame) == 1
    record = store.get("sig-incomplete-timeout")
    assert record.status == "CONFIRMED"
    assert record.timeout_result_at is None
    assert store.summary()["timeout_result"] == 0


@pytest.mark.parametrize(
    ("signal_kwargs", "rows", "expected_state", "expected_attr"),
    [
        (
            {"entry_ref": 100.0, "stop_loss": 95.0, "take_profit": 110.0, "max_hold_bars": 3},
            [
                {"ts": pd.Timestamp("2026-01-01T00:15:00Z"), "open": 100.0, "high": 100.5, "low": 96.0, "close": 94.0, "is_closed": True},
                {"ts": pd.Timestamp("2026-01-01T00:30:00Z"), "open": 94.0, "high": 111.0, "low": 95.5, "close": 109.0, "is_closed": True},
            ],
            "TARGET_REACHED",
            "target_reached_at",
        ),
        (
            {"entry_ref": 100.0, "stop_loss": 92.0, "take_profit": 120.0, "max_hold_bars": 3},
            [
                {"ts": pd.Timestamp("2026-01-01T00:15:00Z"), "open": 100.0, "high": 100.5, "low": 96.0, "close": 94.0, "is_closed": True},
                {"ts": pd.Timestamp("2026-01-01T00:30:00Z"), "open": 94.0, "high": 95.0, "low": 91.0, "close": 93.0, "is_closed": True},
            ],
            "STOP_REACHED",
            "stop_reached_at",
        ),
        (
            {"entry_ref": 100.0, "stop_loss": 92.0, "take_profit": 120.0, "max_hold_bars": 2},
            [
                {"ts": pd.Timestamp("2026-01-01T00:15:00Z"), "open": 100.0, "high": 100.5, "low": 96.0, "close": 94.0, "is_closed": True},
                {"ts": pd.Timestamp("2026-01-01T00:30:00Z"), "open": 94.0, "high": 96.0, "low": 93.0, "close": 95.0, "is_closed": True},
            ],
            "TIMEOUT_RESULT",
            "timeout_result_at",
        ),
        (
            {"entry_ref": 100.0, "stop_loss": 92.0, "take_profit": None, "max_hold_bars": 2},
            [
                {"ts": pd.Timestamp("2026-01-01T00:15:00Z"), "open": 100.0, "high": 100.5, "low": 96.0, "close": 94.0, "is_closed": True},
                {"ts": pd.Timestamp("2026-01-01T00:30:00Z"), "open": 94.0, "high": 96.0, "low": 93.0, "close": 95.0, "is_closed": True},
            ],
            "CENSORED",
            None,
        ),
    ],
)
def test_lifecycle_outcome_advances_after_setup_invalidated(
    tmp_path,
    signal_kwargs,
    rows,
    expected_state,
    expected_attr,
) -> None:
    store = SignalLifecycleStore(tmp_path / "lifecycle.sqlite3")
    signal = _signal(**signal_kwargs)
    store.record_signal(signal, signal_id=f"sig-invalidated-{expected_state}", invalidation_price=97.0)

    assert store.update_symbol("BTC-USDT-SWAP", _frame([rows[0]])) == 1
    record = store.get(f"sig-invalidated-{expected_state}")
    assert record.setup_state == "INVALIDATED"
    assert record.outcome_state == "PENDING_ENTRY"
    assert record.status == "INVALIDATED"

    assert store.update_symbol("BTC-USDT-SWAP", _frame(rows)) == 1
    record = store.get(f"sig-invalidated-{expected_state}")
    payload = lifecycle_payload(record)

    assert record.setup_state == "INVALIDATED"
    assert record.outcome_state == expected_state
    assert record.status == expected_state
    assert payload["setup_state"] == "INVALIDATED"
    assert payload["outcome_state"] == expected_state
    if expected_attr is not None:
        assert getattr(record, expected_attr) == "2026-01-01T00:30:00+00:00"

    with sqlite3.connect(tmp_path / "lifecycle.sqlite3") as conn:
        row = conn.execute(
            "SELECT setup_state, outcome_state, status FROM lifecycle_records WHERE signal_id = ?",
            (f"sig-invalidated-{expected_state}",),
        ).fetchone()
    assert row == ("INVALIDATED", expected_state, expected_state)


def test_active_record_returns_latest_nonterminal_same_side(tmp_path) -> None:
    store = SignalLifecycleStore(tmp_path / "lifecycle.sqlite3")
    first = store.record_signal(_signal(ts="2026-01-01T00:00:00Z"), signal_id="sig-first")
    second = store.record_signal(_signal(ts="2026-01-01T00:15:00Z", entry_ref=101.0), signal_id="sig-second")

    assert first is not None
    assert second is not None
    assert len(store.records) == 2
    assert store.active_record("BTC-USDT-SWAP", "long") is second


def test_lifecycle_persists_records(tmp_path) -> None:
    path = tmp_path / "lifecycle.json"
    store = SignalLifecycleStore(path)
    store.record_signal(_signal(side="short", stop_loss=105.0, entry_ref=100.0), signal_id="sig-4")
    reloaded = SignalLifecycleStore(path)
    record = reloaded.get("sig-4")
    assert record is not None
    assert record.invalidation_price == 105.0
    assert record.analysis_stop_loss == 105.0
    assert record.setup_state == "TRIGGERED"
    assert record.outcome_state == "PENDING_ENTRY"
    assert record.status == "TRIGGERED"
    assert lifecycle_payload(record)["lifecycle_event"] == {
        "type": "TRIGGERED",
        "at": "2026-01-01T00:00:00+00:00",
    }
    json.dumps(lifecycle_payload(record))
    json.dumps(reloaded.summary())


def test_lifecycle_payload_uses_persisted_setup_and_outcome_state(tmp_path) -> None:
    path = tmp_path / "lifecycle.sqlite3"
    store = SignalLifecycleStore(path)
    store.record_signal(_signal(), signal_id="sig-persisted-states")

    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            UPDATE lifecycle_records
            SET setup_state = 'INVALIDATED',
                outcome_state = 'ACTIVE',
                status = 'INVALIDATED',
                confirmed_at = NULL,
                invalidated_at = NULL,
                expired_at = NULL,
                target_reached_at = NULL,
                stop_reached_at = NULL,
                timeout_result_at = NULL
            WHERE signal_id = ?
            """,
            ("sig-persisted-states",),
        )

    reloaded = SignalLifecycleStore(path)
    record = reloaded.get("sig-persisted-states")
    payload = lifecycle_payload(record)

    assert record.setup_state == "INVALIDATED"
    assert record.outcome_state == "ACTIVE"
    assert payload["setup_state"] == "INVALIDATED"
    assert payload["outcome_state"] == "ACTIVE"


def test_lifecycle_persists_dual_states_and_distinct_analysis_stop(tmp_path) -> None:
    path = tmp_path / "lifecycle.sqlite3"
    store = SignalLifecycleStore(path)
    signal = _signal(stop_loss=95.0, take_profit=115.0)
    store.record_signal(signal, signal_id="sig-distinct-stop", invalidation_price=97.0)

    with sqlite3.connect(path) as conn:
        row = conn.execute(
            """
            SELECT setup_state, outcome_state, status, invalidation_price, analysis_stop_loss, take_profit
            FROM lifecycle_records
            WHERE signal_id = ?
            """,
            ("sig-distinct-stop",),
        ).fetchone()

    assert row == ("TRIGGERED", "PENDING_ENTRY", "TRIGGERED", 97.0, 95.0, 115.0)
    record = SignalLifecycleStore(path).get("sig-distinct-stop")
    payload = lifecycle_payload(record)
    assert payload["invalidation_price"] == 97.0
    assert payload["analysis_stop_loss"] == 95.0
    assert payload["stop_loss"] == 95.0


def test_lifecycle_migrates_old_sqlite_status_to_dual_states(tmp_path) -> None:
    path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE lifecycle_records (
                signal_id TEXT PRIMARY KEY,
                inst_id TEXT NOT NULL,
                side TEXT NOT NULL,
                signal_time TEXT NOT NULL,
                entry_ref REAL NOT NULL,
                invalidation_price REAL NOT NULL,
                take_profit REAL,
                max_hold_bars INTEGER NOT NULL,
                status TEXT NOT NULL,
                bars_seen INTEGER NOT NULL DEFAULT 0,
                last_closed_time TEXT,
                last_close REAL,
                confirmed_at TEXT,
                invalidated_at TEXT,
                expired_at TEXT,
                target_reached_at TEXT,
                stop_reached_at TEXT,
                timeout_result_at TEXT,
                last_event_type TEXT NOT NULL,
                last_event_at TEXT NOT NULL,
                signal_timeframe TEXT,
                trend_timeframe TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO lifecycle_records (
                signal_id, inst_id, side, signal_time, entry_ref, invalidation_price,
                take_profit, max_hold_bars, status, bars_seen, target_reached_at,
                last_event_type, last_event_at, created_at, updated_at
            ) VALUES (
                'legacy-target', 'BTC-USDT-SWAP', 'long', '2026-01-01T00:00:00+00:00',
                100.0, 95.0, 115.0, 3, 'TARGET_REACHED', 2,
                '2026-01-01T00:30:00+00:00', 'TARGET_REACHED',
                '2026-01-01T00:30:00+00:00', '2026-01-01T00:00:00+00:00',
                '2026-01-01T00:30:00+00:00'
            )
            """
        )

    store = SignalLifecycleStore(path)
    record = store.get("legacy-target")

    assert record.setup_state == "TRIGGERED"
    assert record.outcome_state == "TARGET_REACHED"
    assert record.status == "TARGET_REACHED"
    assert record.analysis_stop_loss == 95.0


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


def test_lifecycle_max_records_does_not_delete_sqlite_history(tmp_path) -> None:
    path = tmp_path / "lifecycle.sqlite3"
    store = SignalLifecycleStore(path, max_records=5)

    for idx in range(10):
        store.record_signal(
            _signal(ts=f"2026-01-01T0{idx}:00:00Z", entry_ref=100.0 + idx),
            signal_id=f"sig-{idx}",
        )

    assert len(store.records) == 5
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM lifecycle_records").fetchone()[0] == 10
        assert conn.execute("SELECT COUNT(*) FROM lifecycle_events").fetchone()[0] == 10
        assert conn.execute("SELECT COUNT(*) FROM notification_outbox").fetchone()[0] == 10

    reloaded = SignalLifecycleStore(path, max_records=5)
    assert len(reloaded.records) == 5
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM lifecycle_records").fetchone()[0] == 10


def test_lifecycle_sqlite_schema_records_events_and_outbox(tmp_path) -> None:
    path = tmp_path / "lifecycle.sqlite3"
    store = SignalLifecycleStore(path)
    store.record_signal(_signal(take_profit=115.0), signal_id="sig-sqlite")
    store.update_symbol(
        "BTC-USDT-SWAP",
        _frame(
            [
                {
                    "ts": pd.Timestamp("2026-01-01T00:15:00Z"),
                    "open": 100.0,
                    "high": 101.0,
                    "low": 99.0,
                    "close": 101.0,
                    "is_closed": True,
                },
                {
                    "ts": pd.Timestamp("2026-01-01T00:30:00Z"),
                    "open": 101.0,
                    "high": 116.0,
                    "low": 100.5,
                    "close": 112.0,
                    "is_closed": True,
                },
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


def test_lifecycle_outbox_worker_marks_sent_and_failed(tmp_path) -> None:
    path = tmp_path / "lifecycle.sqlite3"
    store = SignalLifecycleStore(path)
    store.enqueue_notification(
        "outbox-ok",
        signal_id=None,
        event_type="TARGET_REACHED",
        payload={"status": "TARGET_REACHED", "symbol": "BTC-USDT-SWAP", "side": "long"},
    )
    store.enqueue_notification(
        "outbox-fail",
        signal_id=None,
        event_type="STOP_REACHED",
        payload={"status": "STOP_REACHED", "symbol": "ETH-USDT-SWAP", "side": "short"},
    )

    class DummyDispatcher:
        def send_lifecycle_event(self, event: dict) -> bool:
            return event["outbox_id"] == "outbox-ok"

    result = LifecycleOutboxWorker(store, DummyDispatcher()).run_once()

    assert result == {"sent": 1, "failed": 1}
    assert store.pending_notifications() == []
    with sqlite3.connect(path) as conn:
        row = conn.execute(
            "SELECT status, attempt_count, last_error, locked_until FROM notification_outbox WHERE outbox_id = ?",
            ("outbox-fail",),
        ).fetchone()
    assert row == ("FAILED", 1, "send_lifecycle_event_returned_false", None)


def test_lifecycle_pending_notifications_only_returns_due_items(tmp_path) -> None:
    path = tmp_path / "lifecycle.sqlite3"
    store = SignalLifecycleStore(path)
    store.enqueue_notification("outbox-due", signal_id=None, event_type="TARGET_REACHED")
    store.enqueue_notification("outbox-future", signal_id=None, event_type="STOP_REACHED")

    with sqlite3.connect(path) as conn:
        conn.execute(
            "UPDATE notification_outbox SET available_at = ? WHERE outbox_id = ?",
            ("2099-01-01T00:00:00+00:00", "outbox-future"),
        )

    assert [item["outbox_id"] for item in store.pending_notifications()] == ["outbox-due"]


def test_lifecycle_claim_pending_notifications_leases_items(tmp_path) -> None:
    path = tmp_path / "lifecycle.sqlite3"
    store = SignalLifecycleStore(path)
    store.enqueue_notification("outbox-claim", signal_id=None, event_type="TARGET_REACHED")

    claimed = store.claim_pending_notifications(limit=10)

    assert [item["outbox_id"] for item in claimed] == ["outbox-claim"]
    assert store.claim_pending_notifications(limit=10) == []
    assert store.pending_notifications() == []
    with sqlite3.connect(path) as conn:
        row = conn.execute(
            "SELECT status, claimed_at, locked_until FROM notification_outbox WHERE outbox_id = ?",
            ("outbox-claim",),
        ).fetchone()
    assert row[0] == "IN_PROGRESS"
    assert row[1]
    assert row[2]


def test_lifecycle_enqueue_does_not_release_claimed_outbox_item(tmp_path) -> None:
    path = tmp_path / "lifecycle.sqlite3"
    store = SignalLifecycleStore(path)
    store.enqueue_notification("outbox-claim", signal_id=None, event_type="TARGET_REACHED")
    store.claim_pending_notifications(limit=10)

    with sqlite3.connect(path) as conn:
        before = conn.execute(
            "SELECT status, available_at, claimed_at, locked_until FROM notification_outbox WHERE outbox_id = ?",
            ("outbox-claim",),
        ).fetchone()

    store.enqueue_notification("outbox-claim", signal_id=None, event_type="TARGET_REACHED", payload={"retry": True})

    with sqlite3.connect(path) as conn:
        after = conn.execute(
            "SELECT status, available_at, claimed_at, locked_until FROM notification_outbox WHERE outbox_id = ?",
            ("outbox-claim",),
        ).fetchone()

    assert after == before
    assert store.pending_notifications() == []


def test_lifecycle_outbox_worker_dead_letters_after_max_attempts(tmp_path) -> None:
    path = tmp_path / "lifecycle.sqlite3"
    store = SignalLifecycleStore(path)
    store.enqueue_notification(
        "outbox-dead",
        signal_id=None,
        event_type="STOP_REACHED",
        payload={"status": "STOP_REACHED", "symbol": "ETH-USDT-SWAP", "side": "short"},
    )

    class DummyDispatcher:
        def send_lifecycle_event(self, _event: dict) -> bool:
            return False

    result = LifecycleOutboxWorker(store, DummyDispatcher(), max_attempts=1).run_once()

    assert result == {"sent": 0, "failed": 0, "dead_letter": 1}
    assert store.pending_notifications() == []
    assert store.summary()["outbox"]["dead_letter"] == 1
    with sqlite3.connect(path) as conn:
        row = conn.execute(
            "SELECT status, attempt_count, last_error FROM notification_outbox WHERE outbox_id = ?",
            ("outbox-dead",),
        ).fetchone()
    assert row == ("DEAD_LETTER", 1, "send_lifecycle_event_returned_false")


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


def test_gui_active_lifecycle_records_hide_terminal_history() -> None:
    from gui import active_lifecycle_records

    records = [
        SimpleNamespace(signal_id="stopped", setup_state="CONFIRMED", outcome_state="STOP_REACHED"),
        SimpleNamespace(signal_id="timeout", setup_state="CONFIRMED", outcome_state="TIMEOUT_RESULT"),
        SimpleNamespace(signal_id="active", setup_state="CONFIRMED", outcome_state="ACTIVE"),
        SimpleNamespace(signal_id="pending", setup_state="TRIGGERED", outcome_state="PENDING_ENTRY"),
    ]

    visible = active_lifecycle_records(records, limit=30)

    assert [record.signal_id for record in visible] == ["active", "pending"]


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
        "2026-01-01 08:00",
    )
