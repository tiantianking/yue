from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from contextlib import closing
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

import pandas as pd

from okx_signal_system.config import project_paths
from okx_signal_system.signal_quality.outcome import SIGNAL_OUTCOME_POLICY, SignalOutcomeSimulator

LifecycleStatus = Literal[
    "TRIGGERED",
    "CONFIRMED",
    "INVALIDATED",
    "EXPIRED",
    "PENDING_ENTRY",
    "ACTIVE",
    "TARGET_REACHED",
    "STOP_REACHED",
    "TIMEOUT_RESULT",
    "CENSORED",
]

SetupState = Literal["TRIGGERED", "CONFIRMED", "INVALIDATED", "EXPIRED"]
OutcomeState = Literal["PENDING_ENTRY", "ACTIVE", "TARGET_REACHED", "STOP_REACHED", "TIMEOUT_RESULT", "CENSORED"]

SETUP_STATES = {"TRIGGERED", "CONFIRMED", "INVALIDATED", "EXPIRED"}
OUTCOME_STATES = {"PENDING_ENTRY", "ACTIVE", "TARGET_REACHED", "STOP_REACHED", "TIMEOUT_RESULT", "CENSORED"}
OUTCOME_TERMINAL_STATES = {"TARGET_REACHED", "STOP_REACHED", "TIMEOUT_RESULT", "CENSORED"}

_OUTCOME_SIMULATOR = SignalOutcomeSimulator()
DEFAULT_LIFECYCLE_OUTBOX_MAX_ATTEMPTS = 3
DEFAULT_LIFECYCLE_OUTBOX_LEASE_SECONDS = 300
DEFAULT_LIFECYCLE_OUTBOX_RETRY_DELAY_SECONDS = 60
MAX_LIFECYCLE_OUTBOX_RETRY_DELAY_SECONDS = 3600
DEFAULT_LIFECYCLE_OUTBOX_POLL_SECONDS = 5.0

log = logging.getLogger(__name__)


@dataclass
class SignalLifecycleRecord:
    signal_id: str
    inst_id: str
    side: str
    signal_time: str
    entry_ref: float
    invalidation_price: float
    max_hold_bars: int
    analysis_stop_loss: float | None = None
    take_profit: float | None = None
    setup_state: SetupState = "TRIGGERED"
    outcome_state: OutcomeState = "PENDING_ENTRY"
    status: LifecycleStatus = "TRIGGERED"
    bars_seen: int = 0
    last_closed_time: str | None = None
    last_close: float | None = None
    confirmed_at: str | None = None
    invalidated_at: str | None = None
    expired_at: str | None = None
    target_reached_at: str | None = None
    stop_reached_at: str | None = None
    timeout_result_at: str | None = None
    last_event_type: str = "TRIGGERED"
    last_event_at: str = ""
    signal_timeframe: str | None = None
    trend_timeframe: str | None = None
    created_at: str = ""
    updated_at: str = ""

    @property
    def ts(self) -> pd.Timestamp:
        return pd.Timestamp(self.signal_time)

    @property
    def stop_loss(self) -> float:
        return float(self.analysis_stop_loss if self.analysis_stop_loss is not None else self.invalidation_price)

    @property
    def accepted(self) -> bool:
        return self.side in {"long", "short"}


def _now_text() -> str:
    return datetime.now(timezone.utc).isoformat()


def _future_text(seconds: int | float) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=float(seconds))).isoformat()


def _timestamp_text(value: Any) -> str:
    return pd.Timestamp(value).isoformat()


def _default_signal_id(signal: Any) -> str:
    ts = _timestamp_text(getattr(signal, "ts"))
    entry = float(getattr(signal, "entry_ref", 0.0) or 0.0)
    return f"{getattr(signal, 'inst_id', '')}:{getattr(signal, 'side', '')}:{ts}:{entry:.8f}"


def _is_closed_value(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip().lower() not in {"", "0", "false", "no"}
    return bool(value)


def lifecycle_payload(record: SignalLifecycleRecord) -> dict[str, Any]:
    return {
        "signal_id": record.signal_id,
        "inst_id": record.inst_id,
        "symbol": record.inst_id,
        "side": record.side,
        "signal_time": record.signal_time,
        "entry_ref": record.entry_ref,
        "setup_state": record.setup_state,
        "outcome_state": record.outcome_state,
        "state": record.status,
        "status": record.status,
        "lifecycle_event": {
            "type": record.last_event_type,
            "at": record.last_event_at or record.updated_at or record.created_at or record.signal_time,
        },
        "triggered_at": record.signal_time,
        "invalidation_price": record.invalidation_price,
        "analysis_stop_loss": record.analysis_stop_loss,
        "stop_loss": record.analysis_stop_loss,
        "take_profit": record.take_profit,
        "target_price": record.take_profit,
        "bars_seen": record.bars_seen,
        "last_closed_time": record.last_closed_time,
        "last_close": record.last_close,
        "confirmed_at": record.confirmed_at,
        "invalidated_at": record.invalidated_at,
        "expired_at": record.expired_at,
        "target_reached_at": record.target_reached_at,
        "stop_reached_at": record.stop_reached_at,
        "timeout_result_at": record.timeout_result_at,
        "last_updated_at": record.updated_at,
        "signal_timeframe": record.signal_timeframe,
        "trend_timeframe": record.trend_timeframe,
    }


class SignalLifecycleStore:
    """Persist setup/outcome lifecycle states using closed candles only."""

    SQLITE_SUFFIXES = {".sqlite", ".sqlite3", ".db"}

    def __init__(self, path: str | Path | None = None, *, max_records: int = 1000):
        default_path = project_paths().output_dir / "signal_lifecycle.sqlite3"
        requested_path = Path(path) if path else default_path
        if requested_path.suffix.lower() == ".json":
            self.path = requested_path.with_suffix(".sqlite3")
            self.legacy_path = requested_path
        else:
            self.path = requested_path
            self.legacy_path = requested_path.with_suffix(".json")
        self.max_records = max_records
        self.records: list[SignalLifecycleRecord] = []
        self._by_id: dict[str, SignalLifecycleRecord] = {}
        self._load()

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS lifecycle_records (
                signal_id TEXT PRIMARY KEY,
                inst_id TEXT NOT NULL,
                side TEXT NOT NULL,
                signal_time TEXT NOT NULL,
                entry_ref REAL NOT NULL,
                invalidation_price REAL NOT NULL,
                analysis_stop_loss REAL,
                take_profit REAL,
                max_hold_bars INTEGER NOT NULL,
                setup_state TEXT NOT NULL DEFAULT 'TRIGGERED',
                outcome_state TEXT NOT NULL DEFAULT 'PENDING_ENTRY',
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
            CREATE TABLE IF NOT EXISTS lifecycle_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                event_at TEXT NOT NULL,
                status TEXT NOT NULL,
                inst_id TEXT NOT NULL,
                side TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(signal_id, event_type, event_at),
                FOREIGN KEY(signal_id) REFERENCES lifecycle_records(signal_id) ON DELETE CASCADE
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS notification_outbox (
                outbox_id TEXT PRIMARY KEY,
                signal_id TEXT,
                channel TEXT NOT NULL,
                event_type TEXT NOT NULL,
                status TEXT NOT NULL,
                available_at TEXT NOT NULL,
                locked_until TEXT,
                claimed_at TEXT,
                attempt_count INTEGER NOT NULL DEFAULT 0,
                sent_at TEXT,
                last_error TEXT,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(signal_id) REFERENCES lifecycle_records(signal_id) ON DELETE SET NULL
            )
            """
        )
        self._ensure_lifecycle_record_columns(conn)
        self._ensure_notification_outbox_columns(conn)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_lifecycle_records_symbol_status ON lifecycle_records(inst_id, status)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_lifecycle_events_signal ON lifecycle_events(signal_id, event_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_notification_outbox_status ON notification_outbox(status, available_at)"
        )
        return conn

    @staticmethod
    def _ensure_lifecycle_record_columns(conn: sqlite3.Connection) -> None:
        existing = {str(row["name"]) for row in conn.execute("PRAGMA table_info(lifecycle_records)").fetchall()}
        columns = {
            "analysis_stop_loss": "REAL",
            "take_profit": "REAL",
            "setup_state": "TEXT NOT NULL DEFAULT 'TRIGGERED'",
            "outcome_state": "TEXT NOT NULL DEFAULT 'PENDING_ENTRY'",
            "target_reached_at": "TEXT",
            "stop_reached_at": "TEXT",
            "timeout_result_at": "TEXT",
        }
        for name, column_type in columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE lifecycle_records ADD COLUMN {name} {column_type}")
        conn.execute(
            """
            UPDATE lifecycle_records
            SET analysis_stop_loss = invalidation_price
            WHERE analysis_stop_loss IS NULL
            """
        )
        conn.execute(
            """
            UPDATE lifecycle_records
            SET setup_state = CASE
                    WHEN status IN ('TRIGGERED', 'CONFIRMED', 'INVALIDATED', 'EXPIRED') THEN status
                    WHEN invalidated_at IS NOT NULL THEN 'INVALIDATED'
                    WHEN expired_at IS NOT NULL THEN 'EXPIRED'
                    WHEN confirmed_at IS NOT NULL THEN 'CONFIRMED'
                    ELSE 'TRIGGERED'
                END,
                outcome_state = CASE
                    WHEN status IN ('TARGET_REACHED', 'STOP_REACHED', 'TIMEOUT_RESULT', 'CENSORED') THEN status
                    WHEN target_reached_at IS NOT NULL THEN 'TARGET_REACHED'
                    WHEN stop_reached_at IS NOT NULL THEN 'STOP_REACHED'
                    WHEN timeout_result_at IS NOT NULL THEN 'TIMEOUT_RESULT'
                    WHEN confirmed_at IS NOT NULL THEN 'ACTIVE'
                    ELSE 'PENDING_ENTRY'
                END
            WHERE setup_state IS NULL
               OR setup_state = ''
               OR outcome_state IS NULL
               OR outcome_state = ''
               OR (setup_state = 'TRIGGERED' AND status IN ('CONFIRMED', 'INVALIDATED', 'EXPIRED'))
               OR (outcome_state = 'PENDING_ENTRY' AND (status IN ('TARGET_REACHED', 'STOP_REACHED', 'TIMEOUT_RESULT', 'CENSORED') OR confirmed_at IS NOT NULL))
               OR status IN ('TARGET_REACHED', 'STOP_REACHED', 'TIMEOUT_RESULT', 'CENSORED')
            """
        )

    @staticmethod
    def _ensure_notification_outbox_columns(conn: sqlite3.Connection) -> None:
        existing = {str(row["name"]) for row in conn.execute("PRAGMA table_info(notification_outbox)").fetchall()}
        columns = {
            "locked_until": "TEXT",
            "claimed_at": "TEXT",
        }
        for name, column_type in columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE notification_outbox ADD COLUMN {name} {column_type}")

    def _load(self) -> None:
        with closing(self._connect()) as conn, conn:
            self._migrate_legacy_json(conn)
            rows = conn.execute(
                """
                SELECT *
                FROM lifecycle_records
                ORDER BY created_at, signal_time, signal_id
                """
            ).fetchall()
        self.records = self._limit_records([self._record_from_row(row) for row in rows])
        self._by_id = {item.signal_id: item for item in self.records}

    def _limit_records(self, records: list[SignalLifecycleRecord]) -> list[SignalLifecycleRecord]:
        if self.max_records <= 0:
            return []
        return records[-self.max_records :]

    def _migrate_legacy_json(self, conn: sqlite3.Connection) -> None:
        if not self.legacy_path.exists():
            return
        existing_count = conn.execute("SELECT COUNT(*) FROM lifecycle_records").fetchone()[0]
        if existing_count:
            return
        try:
            data = json.loads(self.legacy_path.read_text(encoding="utf-8"))
        except Exception:
            return
        if not isinstance(data, list):
            return
        for item in data:
            if not isinstance(item, dict) or not item.get("signal_id"):
                continue
            record = self._record_from_dict(item)
            self._upsert_record(conn, record)
            self._insert_lifecycle_event(
                conn,
                record,
                event_type=record.last_event_type or record.status,
                event_at=record.last_event_at or record.updated_at or record.signal_time,
            )

    @staticmethod
    def _setup_state_from_legacy(item: dict[str, Any]) -> SetupState:
        value = str(item.get("setup_state") or "")
        if value in SETUP_STATES:
            return value  # type: ignore[return-value]
        status = str(item.get("status") or "")
        if status in SETUP_STATES:
            return status  # type: ignore[return-value]
        if item.get("invalidated_at"):
            return "INVALIDATED"
        if item.get("expired_at"):
            return "EXPIRED"
        if item.get("confirmed_at"):
            return "CONFIRMED"
        return "TRIGGERED"

    @staticmethod
    def _outcome_state_from_legacy(item: dict[str, Any], setup_state: str) -> OutcomeState:
        value = str(item.get("outcome_state") or "")
        if value in OUTCOME_STATES:
            return value  # type: ignore[return-value]
        status = str(item.get("status") or "")
        if status in OUTCOME_STATES:
            return status  # type: ignore[return-value]
        if item.get("target_reached_at"):
            return "TARGET_REACHED"
        if item.get("stop_reached_at"):
            return "STOP_REACHED"
        if item.get("timeout_result_at"):
            return "TIMEOUT_RESULT"
        if setup_state == "CONFIRMED":
            return "ACTIVE"
        return "PENDING_ENTRY"

    @staticmethod
    def _compat_status(setup_state: str, outcome_state: str) -> LifecycleStatus:
        if outcome_state in OUTCOME_TERMINAL_STATES:
            return outcome_state  # type: ignore[return-value]
        return setup_state  # type: ignore[return-value]

    @staticmethod
    def _record_from_dict(item: dict[str, Any]) -> SignalLifecycleRecord:
        now = _now_text()
        setup_state = SignalLifecycleStore._setup_state_from_legacy(item)
        outcome_state = SignalLifecycleStore._outcome_state_from_legacy(item, setup_state)
        status = SignalLifecycleStore._compat_status(setup_state, outcome_state)
        invalidation_price = float(item.get("invalidation_price", 0.0) or 0.0)
        analysis_stop_loss = item.get("analysis_stop_loss", item.get("stop_loss"))
        return SignalLifecycleRecord(
            signal_id=str(item["signal_id"]),
            inst_id=str(item.get("inst_id", "")),
            side=str(item.get("side", "")),
            signal_time=str(item.get("signal_time") or item.get("triggered_at") or ""),
            entry_ref=float(item.get("entry_ref", 0.0) or 0.0),
            invalidation_price=invalidation_price,
            analysis_stop_loss=float(analysis_stop_loss) if analysis_stop_loss is not None else invalidation_price,
            take_profit=float(item["take_profit"]) if item.get("take_profit") is not None else None,
            max_hold_bars=int(item.get("max_hold_bars", 0) or 0),
            setup_state=setup_state,
            outcome_state=outcome_state,
            status=status,
            bars_seen=int(item.get("bars_seen", 0) or 0),
            last_closed_time=item.get("last_closed_time"),
            last_close=float(item["last_close"]) if item.get("last_close") is not None else None,
            confirmed_at=item.get("confirmed_at"),
            invalidated_at=item.get("invalidated_at"),
            expired_at=item.get("expired_at"),
            target_reached_at=item.get("target_reached_at"),
            stop_reached_at=item.get("stop_reached_at"),
            timeout_result_at=item.get("timeout_result_at"),
            last_event_type=str(item.get("last_event_type") or item.get("status") or "TRIGGERED"),
            last_event_at=str(item.get("last_event_at") or item.get("updated_at") or item.get("signal_time") or ""),
            signal_timeframe=item.get("signal_timeframe"),
            trend_timeframe=item.get("trend_timeframe"),
            created_at=str(item.get("created_at") or now),
            updated_at=str(item.get("updated_at") or now),
        )

    @staticmethod
    def _record_from_row(row: sqlite3.Row) -> SignalLifecycleRecord:
        item = dict(row)
        setup_state = SignalLifecycleStore._setup_state_from_legacy(item)
        outcome_state = SignalLifecycleStore._outcome_state_from_legacy(item, setup_state)
        status = SignalLifecycleStore._compat_status(setup_state, outcome_state)
        return SignalLifecycleRecord(
            signal_id=str(row["signal_id"]),
            inst_id=str(row["inst_id"]),
            side=str(row["side"]),
            signal_time=str(row["signal_time"]),
            entry_ref=float(row["entry_ref"]),
            invalidation_price=float(row["invalidation_price"]),
            analysis_stop_loss=float(row["analysis_stop_loss"]) if row["analysis_stop_loss"] is not None else float(row["invalidation_price"]),
            take_profit=float(row["take_profit"]) if row["take_profit"] is not None else None,
            max_hold_bars=int(row["max_hold_bars"]),
            setup_state=setup_state,
            outcome_state=outcome_state,
            status=status,
            bars_seen=int(row["bars_seen"]),
            last_closed_time=row["last_closed_time"],
            last_close=float(row["last_close"]) if row["last_close"] is not None else None,
            confirmed_at=row["confirmed_at"],
            invalidated_at=row["invalidated_at"],
            expired_at=row["expired_at"],
            target_reached_at=row["target_reached_at"],
            stop_reached_at=row["stop_reached_at"],
            timeout_result_at=row["timeout_result_at"],
            last_event_type=str(row["last_event_type"]),
            last_event_at=str(row["last_event_at"]),
            signal_timeframe=row["signal_timeframe"],
            trend_timeframe=row["trend_timeframe"],
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    def _save(self) -> None:
        self.records = self._limit_records(self.records)
        self._by_id = {item.signal_id: item for item in self.records}
        with closing(self._connect()) as conn, conn:
            for record in self.records:
                self._upsert_record(conn, record)

    @staticmethod
    def _upsert_record(conn: sqlite3.Connection, record: SignalLifecycleRecord) -> None:
        conn.execute(
            """
            INSERT INTO lifecycle_records (
                signal_id, inst_id, side, signal_time, entry_ref, invalidation_price,
                analysis_stop_loss, take_profit, max_hold_bars, setup_state, outcome_state,
                status, bars_seen, last_closed_time, last_close,
                confirmed_at, invalidated_at, expired_at, target_reached_at, stop_reached_at,
                timeout_result_at, last_event_type, last_event_at, signal_timeframe,
                trend_timeframe, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(signal_id) DO UPDATE SET
                inst_id = excluded.inst_id,
                side = excluded.side,
                signal_time = excluded.signal_time,
                entry_ref = excluded.entry_ref,
                invalidation_price = excluded.invalidation_price,
                analysis_stop_loss = excluded.analysis_stop_loss,
                take_profit = excluded.take_profit,
                max_hold_bars = excluded.max_hold_bars,
                setup_state = excluded.setup_state,
                outcome_state = excluded.outcome_state,
                status = excluded.status,
                bars_seen = excluded.bars_seen,
                last_closed_time = excluded.last_closed_time,
                last_close = excluded.last_close,
                confirmed_at = excluded.confirmed_at,
                invalidated_at = excluded.invalidated_at,
                expired_at = excluded.expired_at,
                target_reached_at = excluded.target_reached_at,
                stop_reached_at = excluded.stop_reached_at,
                timeout_result_at = excluded.timeout_result_at,
                last_event_type = excluded.last_event_type,
                last_event_at = excluded.last_event_at,
                signal_timeframe = excluded.signal_timeframe,
                trend_timeframe = excluded.trend_timeframe,
                created_at = excluded.created_at,
                updated_at = excluded.updated_at
            """,
            (
                record.signal_id,
                record.inst_id,
                record.side,
                record.signal_time,
                record.entry_ref,
                record.invalidation_price,
                record.analysis_stop_loss,
                record.take_profit,
                record.max_hold_bars,
                record.setup_state,
                record.outcome_state,
                record.status,
                record.bars_seen,
                record.last_closed_time,
                record.last_close,
                record.confirmed_at,
                record.invalidated_at,
                record.expired_at,
                record.target_reached_at,
                record.stop_reached_at,
                record.timeout_result_at,
                record.last_event_type,
                record.last_event_at,
                record.signal_timeframe,
                record.trend_timeframe,
                record.created_at,
                record.updated_at,
            ),
        )

    @staticmethod
    def _insert_lifecycle_event(
        conn: sqlite3.Connection,
        record: SignalLifecycleRecord,
        *,
        event_type: str,
        event_at: str,
        status: str | None = None,
        setup_state: str | None = None,
        outcome_state: str | None = None,
    ) -> None:
        event_status = status or record.status
        payload = lifecycle_payload(record)
        payload["state"] = event_status
        payload["status"] = event_status
        if setup_state is not None:
            payload["setup_state"] = setup_state
        if outcome_state is not None:
            payload["outcome_state"] = outcome_state
        payload["lifecycle_event"] = {"type": event_type, "at": event_at}
        payload_json = json.dumps(payload, ensure_ascii=False)
        conn.execute(
            """
            INSERT OR IGNORE INTO lifecycle_events (
                signal_id, event_type, event_at, status, inst_id, side, payload_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.signal_id,
                event_type,
                event_at,
                event_status,
                record.inst_id,
                record.side,
                payload_json,
                _now_text(),
            ),
        )
        outbox_id = f"{record.signal_id}:{event_type}:{event_at}"
        conn.execute(
            """
            INSERT INTO notification_outbox (
                outbox_id, signal_id, channel, event_type, status, available_at,
                locked_until, claimed_at, attempt_count, sent_at, last_error,
                payload_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, 'PENDING', ?, NULL, NULL, 0, NULL, NULL, ?, ?, ?)
            ON CONFLICT(outbox_id) DO UPDATE SET
                signal_id = excluded.signal_id,
                channel = excluded.channel,
                event_type = excluded.event_type,
                status = CASE
                    WHEN notification_outbox.status IN ('SENT', 'DEAD_LETTER', 'IN_PROGRESS') THEN notification_outbox.status
                    ELSE 'PENDING'
                END,
                available_at = CASE
                    WHEN notification_outbox.status IN ('SENT', 'DEAD_LETTER', 'IN_PROGRESS') THEN notification_outbox.available_at
                    ELSE excluded.available_at
                END,
                locked_until = CASE
                    WHEN notification_outbox.status IN ('SENT', 'DEAD_LETTER', 'IN_PROGRESS') THEN notification_outbox.locked_until
                    ELSE NULL
                END,
                claimed_at = CASE
                    WHEN notification_outbox.status IN ('SENT', 'DEAD_LETTER', 'IN_PROGRESS') THEN notification_outbox.claimed_at
                    ELSE NULL
                END,
                last_error = CASE
                    WHEN notification_outbox.status IN ('SENT', 'DEAD_LETTER', 'IN_PROGRESS') THEN notification_outbox.last_error
                    ELSE NULL
                END,
                payload_json = excluded.payload_json,
                updated_at = excluded.updated_at
            """,
            (
                outbox_id,
                record.signal_id,
                "feishu",
                event_type,
                event_at,
                payload_json,
                _now_text(),
                _now_text(),
            ),
        )

    def record_signal(
        self,
        signal: Any,
        *,
        signal_id: str | None = None,
        invalidation_price: float | None = None,
        take_profit: float | None = None,
        signal_timeframe: str | None = None,
        trend_timeframe: str | None = None,
    ) -> SignalLifecycleRecord | None:
        if not bool(getattr(signal, "accepted", False)):
            return None
        side = str(getattr(signal, "side", ""))
        if side not in {"long", "short"}:
            return None
        entry_ref = getattr(signal, "entry_ref", None)
        invalidation = invalidation_price if invalidation_price is not None else getattr(signal, "stop_loss", None)
        analysis_stop = getattr(signal, "stop_loss", None)
        if entry_ref is None or invalidation is None:
            return None
        sid = signal_id or _default_signal_id(signal)
        existing = self._by_id.get(sid)
        if existing is not None:
            return existing
        persisted = self._load_record(sid)
        if persisted is not None:
            return persisted
        now = _now_text()
        signal_time = _timestamp_text(getattr(signal, "ts"))
        target_price = take_profit if take_profit is not None else getattr(signal, "take_profit", None)
        record = SignalLifecycleRecord(
            signal_id=sid,
            inst_id=str(getattr(signal, "inst_id", "")),
            side=side,
            signal_time=signal_time,
            entry_ref=float(entry_ref),
            invalidation_price=float(invalidation),
            analysis_stop_loss=float(analysis_stop) if analysis_stop is not None else float(invalidation),
            take_profit=float(target_price) if target_price is not None else None,
            max_hold_bars=int(getattr(signal, "max_hold_bars", 0) or 0),
            signal_timeframe=signal_timeframe,
            trend_timeframe=trend_timeframe,
            created_at=now,
            updated_at=now,
            last_event_at=signal_time,
        )
        self.records.append(record)
        self.records = self._limit_records(self.records)
        self._by_id = {item.signal_id: item for item in self.records}
        with closing(self._connect()) as conn, conn:
            self._upsert_record(conn, record)
            self._insert_lifecycle_event(conn, record, event_type="TRIGGERED", event_at=signal_time)
        return record

    def _load_record(self, signal_id: str) -> SignalLifecycleRecord | None:
        with closing(self._connect()) as conn, conn:
            row = conn.execute(
                "SELECT * FROM lifecycle_records WHERE signal_id = ?",
                (signal_id,),
            ).fetchone()
        if row is None:
            return None
        return self._record_from_row(row)

    def update_symbol(self, inst_id: str, frame: pd.DataFrame) -> int:
        df = self._closed_frame(frame)
        if df.empty:
            return 0
        updated = 0
        lifecycle_events: list[tuple[SignalLifecycleRecord, str, str, str, str, str]] = []
        for record in self.records:
            if record.inst_id != inst_id:
                continue
            if record.setup_state not in {"TRIGGERED", "CONFIRMED"} and record.outcome_state in OUTCOME_TERMINAL_STATES:
                continue
            if self._update_record(record, df, lifecycle_events):
                updated += 1
        if updated:
            self._save()
            with closing(self._connect()) as conn, conn:
                for record, event_type, event_at, status, setup_state, outcome_state in lifecycle_events:
                    self._insert_lifecycle_event(
                        conn,
                        record,
                        event_type=event_type,
                        event_at=event_at,
                        status=status,
                        setup_state=setup_state,
                        outcome_state=outcome_state,
                    )
        return updated

    def enqueue_notification(
        self,
        outbox_id: str,
        *,
        signal_id: str | None,
        event_type: str,
        payload: dict[str, Any] | None = None,
        channel: str = "feishu",
    ) -> bool:
        if channel == "feishu":
            from okx_signal_system.config import feishu_notifications_enabled

            if not feishu_notifications_enabled(True):
                log.info("Feishu outbox enqueue suppressed by FEISHU_ENABLED: %s", outbox_id)
                return False
        payload = payload or {}
        now = _now_text()
        with closing(self._connect()) as conn, conn:
            conn.execute(
                """
                INSERT INTO notification_outbox (
                    outbox_id, signal_id, channel, event_type, status, available_at,
                    locked_until, claimed_at, attempt_count, sent_at, last_error,
                    payload_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'PENDING', ?, NULL, NULL, 0, NULL, NULL, ?, ?, ?)
                ON CONFLICT(outbox_id) DO UPDATE SET
                    signal_id = excluded.signal_id,
                    channel = excluded.channel,
                    event_type = excluded.event_type,
                    status = CASE
                        WHEN notification_outbox.status IN ('SENT', 'DEAD_LETTER', 'IN_PROGRESS') THEN notification_outbox.status
                        ELSE 'PENDING'
                    END,
                    available_at = CASE
                        WHEN notification_outbox.status IN ('SENT', 'DEAD_LETTER', 'IN_PROGRESS') THEN notification_outbox.available_at
                        ELSE excluded.available_at
                    END,
                    locked_until = CASE
                        WHEN notification_outbox.status IN ('SENT', 'DEAD_LETTER', 'IN_PROGRESS') THEN notification_outbox.locked_until
                        ELSE NULL
                    END,
                    claimed_at = CASE
                        WHEN notification_outbox.status IN ('SENT', 'DEAD_LETTER', 'IN_PROGRESS') THEN notification_outbox.claimed_at
                        ELSE NULL
                    END,
                    last_error = CASE
                        WHEN notification_outbox.status IN ('SENT', 'DEAD_LETTER', 'IN_PROGRESS') THEN notification_outbox.last_error
                        ELSE NULL
                    END,
                    payload_json = excluded.payload_json,
                    updated_at = excluded.updated_at
                """,
                (
                    outbox_id,
                    signal_id,
                    channel,
                    event_type,
                    now,
                    json.dumps(payload, ensure_ascii=False, default=str),
                    now,
                    now,
                ),
            )
        return True

    def mark_notification_sent(self, outbox_id: str) -> None:
        now = _now_text()
        with closing(self._connect()) as conn, conn:
            conn.execute(
                """
                UPDATE notification_outbox
                SET status = 'SENT',
                    sent_at = ?,
                    last_error = NULL,
                    locked_until = NULL,
                    updated_at = ?
                WHERE outbox_id = ?
                   OR (signal_id = ? AND event_type = 'TRIGGERED')
                """,
                (now, now, outbox_id, outbox_id),
            )

    def mark_notification_failed(self, outbox_id: str, error: str) -> None:
        now = _now_text()
        with closing(self._connect()) as conn, conn:
            row = conn.execute(
                """
                SELECT attempt_count
                FROM notification_outbox
                WHERE outbox_id = ?
                   OR (signal_id = ? AND event_type = 'TRIGGERED')
                ORDER BY CASE WHEN outbox_id = ? THEN 0 ELSE 1 END
                LIMIT 1
                """,
                (outbox_id, outbox_id, outbox_id),
            ).fetchone()
            attempt_count = int(row["attempt_count"]) if row is not None else 0
            retry_delay = min(
                DEFAULT_LIFECYCLE_OUTBOX_RETRY_DELAY_SECONDS * (2**attempt_count),
                MAX_LIFECYCLE_OUTBOX_RETRY_DELAY_SECONDS,
            )
            conn.execute(
                """
                UPDATE notification_outbox
                SET status = 'FAILED',
                    last_error = ?,
                    available_at = ?,
                    locked_until = NULL,
                    attempt_count = attempt_count + 1,
                    updated_at = ?
                WHERE (outbox_id = ?
                   OR (signal_id = ? AND event_type = 'TRIGGERED'))
                  AND status NOT IN ('SENT', 'FAILED', 'DEAD_LETTER')
                """,
                (error[:1000], _future_text(retry_delay), now, outbox_id, outbox_id),
            )

    def mark_notification_dead_letter(self, outbox_id: str, error: str) -> None:
        now = _now_text()
        with closing(self._connect()) as conn, conn:
            conn.execute(
                """
                UPDATE notification_outbox
                SET status = 'DEAD_LETTER',
                    last_error = ?,
                    locked_until = NULL,
                    attempt_count = attempt_count + 1,
                    updated_at = ?
                WHERE outbox_id = ?
                """,
                (error[:1000], now, outbox_id),
            )

    def pending_notifications(self, *, limit: int = 100) -> list[dict[str, Any]]:
        now = _now_text()
        with closing(self._connect()) as conn, conn:
            rows = conn.execute(
                """
                SELECT *
                FROM notification_outbox
                WHERE status IN ('PENDING', 'FAILED')
                  AND available_at <= ?
                  AND (locked_until IS NULL OR locked_until <= ?)
                ORDER BY available_at, created_at
                LIMIT ?
                """,
                (now, now, int(limit)),
            ).fetchall()
        return [self._outbox_row(row) for row in rows]

    def claim_pending_notifications(
        self,
        *,
        limit: int = 100,
        lease_seconds: int = DEFAULT_LIFECYCLE_OUTBOX_LEASE_SECONDS,
    ) -> list[dict[str, Any]]:
        now = _now_text()
        locked_until = _future_text(lease_seconds)
        with closing(self._connect()) as conn, conn:
            conn.commit()
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                """
                SELECT outbox_id
                FROM notification_outbox
                WHERE (
                    status IN ('PENDING', 'FAILED')
                    OR (status = 'IN_PROGRESS' AND locked_until IS NOT NULL AND locked_until <= ?)
                )
                  AND available_at <= ?
                  AND (locked_until IS NULL OR locked_until <= ?)
                ORDER BY available_at, created_at
                LIMIT ?
                """,
                (now, now, now, int(limit)),
            ).fetchall()
            outbox_ids = [str(row["outbox_id"]) for row in rows]
            if not outbox_ids:
                return []
            placeholders = ",".join("?" for _ in outbox_ids)
            conn.execute(
                f"""
                UPDATE notification_outbox
                SET status = 'IN_PROGRESS',
                    claimed_at = ?,
                    locked_until = ?,
                    updated_at = ?
                WHERE outbox_id IN ({placeholders})
                """,
                [now, locked_until, now, *outbox_ids],
            )
            claimed = conn.execute(
                f"""
                SELECT *
                FROM notification_outbox
                WHERE outbox_id IN ({placeholders})
                ORDER BY available_at, created_at
                """,
                outbox_ids,
            ).fetchall()
        return [self._outbox_row(row) for row in claimed]

    def outbox_summary(self) -> dict[str, Any]:
        with closing(self._connect()) as conn, conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) AS count FROM notification_outbox GROUP BY status"
            ).fetchall()
            latest_updated = conn.execute(
                "SELECT MAX(updated_at) FROM notification_outbox"
            ).fetchone()[0]
        counts = {str(row["status"]).lower(): int(row["count"]) for row in rows}
        return {
            "pending": counts.get("pending", 0),
            "sent": counts.get("sent", 0),
            "failed": counts.get("failed", 0),
            "in_progress": counts.get("in_progress", 0),
            "dead_letter": counts.get("dead_letter", 0),
            "updated_at": latest_updated,
        }

    @staticmethod
    def _outbox_row(row: sqlite3.Row) -> dict[str, Any]:
        payload_text = row["payload_json"]
        try:
            payload = json.loads(payload_text)
        except Exception:
            payload = {}
        return {
            "outbox_id": row["outbox_id"],
            "signal_id": row["signal_id"],
            "channel": row["channel"],
            "event_type": row["event_type"],
            "status": row["status"],
            "available_at": row["available_at"],
            "locked_until": row["locked_until"],
            "claimed_at": row["claimed_at"],
            "attempt_count": row["attempt_count"],
            "sent_at": row["sent_at"],
            "last_error": row["last_error"],
            "payload": payload,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def summary(self) -> dict[str, Any]:
        setup_counts = Counter(item.setup_state for item in self.records)
        outcome_counts = Counter(item.outcome_state for item in self.records)
        active_records = sum(
            1
            for item in self.records
            if item.setup_state in {"TRIGGERED", "CONFIRMED"}
            and item.outcome_state not in OUTCOME_TERMINAL_STATES
        )
        terminal_records = len(self.records) - active_records
        latest_updated = max((item.updated_at for item in self.records if item.updated_at), default=None)
        latest_event_at = max(
            (item.last_event_at or item.updated_at or item.created_at for item in self.records if (item.last_event_at or item.updated_at or item.created_at)),
            default=None,
        )
        latest_event_type = None
        if latest_event_at is not None:
            for item in reversed(self.records):
                item_event_at = item.last_event_at or item.updated_at or item.created_at
                if item_event_at == latest_event_at:
                    latest_event_type = item.last_event_type
                    break
        outbox = self.outbox_summary()
        return {
            "total": len(self.records),
            "triggered": setup_counts.get("TRIGGERED", 0),
            "confirmed": setup_counts.get("CONFIRMED", 0),
            "invalidated": setup_counts.get("INVALIDATED", 0),
            "expired": setup_counts.get("EXPIRED", 0),
            "pending_entry": outcome_counts.get("PENDING_ENTRY", 0),
            "outcome_active": outcome_counts.get("ACTIVE", 0),
            "target_reached": outcome_counts.get("TARGET_REACHED", 0),
            "stop_reached": outcome_counts.get("STOP_REACHED", 0),
            "timeout_result": outcome_counts.get("TIMEOUT_RESULT", 0),
            "censored": outcome_counts.get("CENSORED", 0),
            "active": active_records,
            "terminal": terminal_records,
            "latest_event_type": latest_event_type,
            "latest_event_at": latest_event_at,
            "outbox": outbox,
            "updated_at": latest_updated or _now_text(),
        }

    def get(self, signal_id: str) -> SignalLifecycleRecord | None:
        return self._by_id.get(signal_id)

    def active_record(self, inst_id: str, side: str | None = None) -> SignalLifecycleRecord | None:
        for record in reversed(self.records):
            if record.inst_id != inst_id:
                continue
            if side is not None and record.side != side:
                continue
            if record.setup_state not in {"TRIGGERED", "CONFIRMED"}:
                continue
            if record.outcome_state in OUTCOME_TERMINAL_STATES:
                continue
            return record
        return None

    @staticmethod
    def _closed_frame(frame: pd.DataFrame) -> pd.DataFrame:
        if frame.empty or "ts" not in frame.columns or "close" not in frame.columns:
            return pd.DataFrame()
        df = frame.copy()
        if "is_closed" in df.columns:
            df = df[df["is_closed"].map(_is_closed_value)]
        if df.empty:
            return pd.DataFrame()
        df["ts"] = pd.to_datetime(df["ts"], utc=True, errors="coerce")
        df["close"] = pd.to_numeric(df["close"], errors="coerce")
        for column in ["open", "high", "low"]:
            if column not in df.columns:
                df[column] = df["close"]
            else:
                df[column] = pd.to_numeric(df[column], errors="coerce")
        return df.dropna(subset=["ts", "open", "high", "low", "close"]).sort_values("ts").reset_index(drop=True)

    def _update_record(
        self,
        record: SignalLifecycleRecord,
        df: pd.DataFrame,
        lifecycle_events: list[tuple[SignalLifecycleRecord, str, str, str, str, str]] | None = None,
    ) -> bool:
        start = pd.Timestamp(record.signal_time)
        if start.tzinfo is None:
            start = start.tz_localize("UTC")
        future = df[df["ts"] > start].reset_index(drop=True)
        if future.empty:
            return False

        changed = False
        for idx, row in future.iterrows():
            bars_seen = int(idx) + 1
            close = float(row["close"])
            closed_time = pd.Timestamp(row["ts"]).isoformat()
            was_confirmed = record.setup_state == "CONFIRMED"
            if record.bars_seen != bars_seen or record.last_closed_time != closed_time or record.last_close != close:
                record.bars_seen = bars_seen
                record.last_closed_time = closed_time
                record.last_close = close
                changed = True

            if (
                was_confirmed
                and record.outcome_state not in OUTCOME_TERMINAL_STATES
                and record.take_profit is None
                and self._analysis_stop_reached(record, row)
            ):
                record.outcome_state = "STOP_REACHED"
                record.status = self._compat_status(record.setup_state, record.outcome_state)
                record.stop_reached_at = closed_time
                record.last_event_type = "STOP_REACHED"
                record.last_event_at = closed_time
                record.updated_at = _now_text()
                if lifecycle_events is not None:
                    lifecycle_events.append((record, "STOP_REACHED", closed_time, record.status, record.setup_state, record.outcome_state))
                return True

            result_event = self._research_result_event(record, future.iloc[:bars_seen])
            if result_event is not None and record.outcome_state not in OUTCOME_TERMINAL_STATES:
                if record.setup_state == "TRIGGERED" and self._confirms(record, close):
                    record.setup_state = "CONFIRMED"
                    record.outcome_state = "ACTIVE"
                    record.confirmed_at = closed_time
                    record.status = self._compat_status(record.setup_state, record.outcome_state)
                    record.last_event_type = "CONFIRMED"
                    record.last_event_at = closed_time
                    changed = True
                    if lifecycle_events is not None:
                        lifecycle_events.append((record, "CONFIRMED", closed_time, record.status, record.setup_state, record.outcome_state))
                event_type, attr_name, event_at = result_event
                record.outcome_state = event_type  # type: ignore[assignment]
                record.status = self._compat_status(record.setup_state, record.outcome_state)
                setattr(record, attr_name, event_at)
                record.last_event_type = event_type
                record.last_event_at = event_at
                record.updated_at = _now_text()
                if lifecycle_events is not None:
                    lifecycle_events.append((record, event_type, event_at, record.status, record.setup_state, record.outcome_state))
                return True

            if record.setup_state == "TRIGGERED" and self._invalidates(record, close):
                record.setup_state = "INVALIDATED"
                record.status = self._compat_status(record.setup_state, record.outcome_state)
                record.invalidated_at = closed_time
                record.last_event_type = "INVALIDATED"
                record.last_event_at = closed_time
                record.updated_at = _now_text()
                changed = True
                if lifecycle_events is not None:
                    lifecycle_events.append((record, "INVALIDATED", closed_time, record.status, record.setup_state, record.outcome_state))
                continue

            if record.setup_state == "TRIGGERED" and self._confirms(record, close):
                record.setup_state = "CONFIRMED"
                if record.outcome_state == "PENDING_ENTRY":
                    record.outcome_state = "ACTIVE"
                record.status = self._compat_status(record.setup_state, record.outcome_state)
                record.confirmed_at = closed_time
                record.last_event_type = "CONFIRMED"
                record.last_event_at = closed_time
                changed = True
                if lifecycle_events is not None:
                    lifecycle_events.append((record, "CONFIRMED", closed_time, record.status, record.setup_state, record.outcome_state))

            if record.setup_state == "TRIGGERED" and record.max_hold_bars > 0 and bars_seen >= record.max_hold_bars:
                record.setup_state = "EXPIRED"
                record.status = self._compat_status(record.setup_state, record.outcome_state)
                record.expired_at = closed_time
                record.last_event_type = "EXPIRED"
                record.last_event_at = closed_time
                record.updated_at = _now_text()
                changed = True
                if lifecycle_events is not None:
                    lifecycle_events.append((record, "EXPIRED", closed_time, record.status, record.setup_state, record.outcome_state))

            if (
                record.setup_state == "CONFIRMED"
                and record.outcome_state not in OUTCOME_TERMINAL_STATES
                and record.take_profit is None
                and record.max_hold_bars > 0
                and bars_seen >= record.max_hold_bars
            ):
                record.outcome_state = "TIMEOUT_RESULT"
                record.status = self._compat_status(record.setup_state, record.outcome_state)
                record.timeout_result_at = closed_time
                record.last_event_type = "TIMEOUT_RESULT"
                record.last_event_at = closed_time
                record.updated_at = _now_text()
                changed = True
                if lifecycle_events is not None:
                    lifecycle_events.append((record, "TIMEOUT_RESULT", closed_time, record.status, record.setup_state, record.outcome_state))
                return True

            if (
                record.setup_state == "INVALIDATED"
                and record.outcome_state not in OUTCOME_TERMINAL_STATES
                and record.max_hold_bars > 0
                and bars_seen >= record.max_hold_bars
            ):
                record.outcome_state = "CENSORED"
                record.status = self._compat_status(record.setup_state, record.outcome_state)
                record.last_event_type = "CENSORED"
                record.last_event_at = closed_time
                record.updated_at = _now_text()
                changed = True
                if lifecycle_events is not None:
                    lifecycle_events.append((record, "CENSORED", closed_time, record.status, record.setup_state, record.outcome_state))

        if changed:
            record.updated_at = _now_text()
            record.status = self._compat_status(record.setup_state, record.outcome_state)
        return changed

    @staticmethod
    def _confirms(record: SignalLifecycleRecord, close: float) -> bool:
        if record.side == "long":
            return close >= record.entry_ref
        return close <= record.entry_ref

    @staticmethod
    def _invalidates(record: SignalLifecycleRecord, close: float) -> bool:
        if record.side == "long":
            return close <= record.invalidation_price
        return close >= record.invalidation_price

    @staticmethod
    def _analysis_stop_reached(record: SignalLifecycleRecord, row: pd.Series) -> bool:
        stop = float(record.stop_loss)
        if record.side == "long":
            return float(row["low"]) <= stop
        return float(row["high"]) >= stop

    def _research_result_event(self, record: SignalLifecycleRecord, frame: pd.DataFrame) -> tuple[str, str, str] | None:
        if record.take_profit is None or record.max_hold_bars <= 0:
            return None
        result = _OUTCOME_SIMULATOR.simulate_signal(
            record,
            frame=frame,
            closed_only=True,
            after_signal_time=False,
            policy=SIGNAL_OUTCOME_POLICY,
            require_complete_timeout=True,
        )
        if result is None:
            return None
        event_at = pd.Timestamp(result.exit_time).isoformat()
        if result.outcome == "TP":
            return "TARGET_REACHED", "target_reached_at", event_at
        if result.outcome == "SL":
            return "STOP_REACHED", "stop_reached_at", event_at
        if result.outcome == "TIMEOUT":
            return "TIMEOUT_RESULT", "timeout_result_at", event_at
        return None


class LifecycleOutboxWorker:
    def __init__(
        self,
        store: SignalLifecycleStore,
        dispatcher: Any,
        *,
        max_attempts: int = DEFAULT_LIFECYCLE_OUTBOX_MAX_ATTEMPTS,
    ):
        self.store = store
        self.dispatcher = dispatcher
        self.max_attempts = max(1, int(max_attempts))

    def run_once(self, *, limit: int = 100) -> dict[str, int]:
        summary = {"sent": 0, "failed": 0}
        from okx_signal_system.config import feishu_notifications_enabled

        if not feishu_notifications_enabled(True):
            return summary
        for item in self.store.claim_pending_notifications(limit=limit):
            outbox_id = str(item["outbox_id"])
            try:
                sent = bool(self.dispatcher.send_lifecycle_event(item))
            except Exception as exc:
                self._mark_failed(item, str(exc), summary)
                continue
            if sent:
                self.store.mark_notification_sent(outbox_id)
                summary["sent"] += 1
            else:
                self._mark_failed(item, "send_lifecycle_event_returned_false", summary)
        return summary

    async def run_forever(
        self,
        *,
        interval_seconds: float = DEFAULT_LIFECYCLE_OUTBOX_POLL_SECONDS,
        limit: int = 100,
    ) -> None:
        """Continuously drain the durable notification outbox until cancelled."""
        delay = max(1.0, float(interval_seconds))
        while True:
            try:
                summary = await asyncio.to_thread(self.run_once, limit=limit)
                if summary.get("failed") or summary.get("dead_letter"):
                    log.warning("notification outbox worker summary: %s", summary)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("notification outbox worker cycle failed")
            await asyncio.sleep(delay)

    def _mark_failed(self, item: dict[str, Any], error: str, summary: dict[str, int]) -> None:
        outbox_id = str(item["outbox_id"])
        attempt_count = int(item.get("attempt_count") or 0)
        if attempt_count + 1 >= self.max_attempts:
            self.store.mark_notification_dead_letter(outbox_id, error)
            summary["dead_letter"] = summary.get("dead_letter", 0) + 1
            return
        self.store.mark_notification_failed(outbox_id, error)
        summary["failed"] += 1


__all__ = [
    "LifecycleStatus",
    "DEFAULT_LIFECYCLE_OUTBOX_MAX_ATTEMPTS",
    "DEFAULT_LIFECYCLE_OUTBOX_POLL_SECONDS",
    "LifecycleOutboxWorker",
    "SignalLifecycleRecord",
    "SignalLifecycleStore",
    "lifecycle_payload",
]
