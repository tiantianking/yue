from __future__ import annotations

import json
import sqlite3
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import pandas as pd

from okx_signal_system.config import project_paths

LifecycleStatus = Literal[
    "TRIGGERED",
    "CONFIRMED",
    "INVALIDATED",
    "EXPIRED",
    "TARGET_REACHED",
    "STOP_REACHED",
    "TIMEOUT_RESULT",
]


@dataclass
class SignalLifecycleRecord:
    signal_id: str
    inst_id: str
    side: str
    signal_time: str
    entry_ref: float
    invalidation_price: float
    max_hold_bars: int
    take_profit: float | None = None
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


def _now_text() -> str:
    return datetime.now(timezone.utc).isoformat()


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
        "state": record.status,
        "status": record.status,
        "lifecycle_event": {
            "type": record.last_event_type,
            "at": record.last_event_at or record.updated_at or record.created_at or record.signal_time,
        },
        "triggered_at": record.signal_time,
        "invalidation_price": record.invalidation_price,
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
    }


class SignalLifecycleStore:
    """Persist and update signal lifecycle states using closed candles only."""

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
            "take_profit": "REAL",
            "target_reached_at": "TEXT",
            "stop_reached_at": "TEXT",
            "timeout_result_at": "TEXT",
        }
        for name, column_type in columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE lifecycle_records ADD COLUMN {name} {column_type}")

    def _load(self) -> None:
        with self._connect() as conn:
            self._migrate_legacy_json(conn)
            rows = conn.execute(
                """
                SELECT *
                FROM lifecycle_records
                ORDER BY created_at, signal_time, signal_id
                """
            ).fetchall()
        self.records = [self._record_from_row(row) for row in rows]
        self._by_id = {item.signal_id: item for item in self.records}

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
    def _record_from_dict(item: dict[str, Any]) -> SignalLifecycleRecord:
        now = _now_text()
        return SignalLifecycleRecord(
            signal_id=str(item["signal_id"]),
            inst_id=str(item.get("inst_id", "")),
            side=str(item.get("side", "")),
            signal_time=str(item.get("signal_time") or item.get("triggered_at") or ""),
            entry_ref=float(item.get("entry_ref", 0.0) or 0.0),
            invalidation_price=float(item.get("invalidation_price", 0.0) or 0.0),
            take_profit=float(item["take_profit"]) if item.get("take_profit") is not None else None,
            max_hold_bars=int(item.get("max_hold_bars", 0) or 0),
            status=str(item.get("status", "TRIGGERED")),  # type: ignore[arg-type]
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
        return SignalLifecycleRecord(
            signal_id=str(row["signal_id"]),
            inst_id=str(row["inst_id"]),
            side=str(row["side"]),
            signal_time=str(row["signal_time"]),
            entry_ref=float(row["entry_ref"]),
            invalidation_price=float(row["invalidation_price"]),
            take_profit=float(row["take_profit"]) if row["take_profit"] is not None else None,
            max_hold_bars=int(row["max_hold_bars"]),
            status=str(row["status"]),  # type: ignore[arg-type]
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
        self.records = self.records[-self.max_records :]
        self._by_id = {item.signal_id: item for item in self.records}
        keep_ids = [item.signal_id for item in self.records]
        with self._connect() as conn:
            for record in self.records:
                self._upsert_record(conn, record)
            if keep_ids:
                placeholders = ",".join("?" for _ in keep_ids)
                conn.execute(
                    f"DELETE FROM lifecycle_records WHERE signal_id NOT IN ({placeholders})",
                    keep_ids,
                )

    @staticmethod
    def _upsert_record(conn: sqlite3.Connection, record: SignalLifecycleRecord) -> None:
        conn.execute(
            """
            INSERT INTO lifecycle_records (
                signal_id, inst_id, side, signal_time, entry_ref, invalidation_price,
                take_profit, max_hold_bars, status, bars_seen, last_closed_time, last_close,
                confirmed_at, invalidated_at, expired_at, target_reached_at, stop_reached_at,
                timeout_result_at, last_event_type, last_event_at, signal_timeframe,
                trend_timeframe, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(signal_id) DO UPDATE SET
                inst_id = excluded.inst_id,
                side = excluded.side,
                signal_time = excluded.signal_time,
                entry_ref = excluded.entry_ref,
                invalidation_price = excluded.invalidation_price,
                take_profit = excluded.take_profit,
                max_hold_bars = excluded.max_hold_bars,
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
                record.take_profit,
                record.max_hold_bars,
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
    ) -> None:
        event_status = status or record.status
        payload = lifecycle_payload(record)
        payload["state"] = event_status
        payload["status"] = event_status
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
                attempt_count, sent_at, last_error, payload_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, 'PENDING', ?, 0, NULL, NULL, ?, ?, ?)
            ON CONFLICT(outbox_id) DO UPDATE SET
                signal_id = excluded.signal_id,
                channel = excluded.channel,
                event_type = excluded.event_type,
                status = CASE
                    WHEN notification_outbox.status = 'SENT' THEN notification_outbox.status
                    ELSE 'PENDING'
                END,
                available_at = excluded.available_at,
                last_error = CASE
                    WHEN notification_outbox.status = 'SENT' THEN notification_outbox.last_error
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
        if entry_ref is None or invalidation is None:
            return None
        sid = signal_id or _default_signal_id(signal)
        existing = self._by_id.get(sid)
        if existing is not None:
            return existing
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
            take_profit=float(target_price) if target_price is not None else None,
            max_hold_bars=int(getattr(signal, "max_hold_bars", 0) or 0),
            signal_timeframe=signal_timeframe,
            trend_timeframe=trend_timeframe,
            created_at=now,
            updated_at=now,
            last_event_at=signal_time,
        )
        self.records.append(record)
        self.records = self.records[-self.max_records :]
        self._by_id = {item.signal_id: item for item in self.records}
        with self._connect() as conn:
            self._upsert_record(conn, record)
            self._insert_lifecycle_event(conn, record, event_type="TRIGGERED", event_at=signal_time)
        return record

    def update_symbol(self, inst_id: str, frame: pd.DataFrame) -> int:
        df = self._closed_frame(frame)
        if df.empty:
            return 0
        updated = 0
        lifecycle_events: list[tuple[SignalLifecycleRecord, str, str, str]] = []
        for record in self.records:
            if record.inst_id != inst_id or record.status not in {"TRIGGERED", "CONFIRMED"}:
                continue
            if self._update_record(record, df, lifecycle_events):
                updated += 1
        if updated:
            self._save()
            with self._connect() as conn:
                for record, event_type, event_at, status in lifecycle_events:
                    self._insert_lifecycle_event(
                        conn,
                        record,
                        event_type=event_type,
                        event_at=event_at,
                        status=status,
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
    ) -> None:
        payload = payload or {}
        now = _now_text()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO notification_outbox (
                    outbox_id, signal_id, channel, event_type, status, available_at,
                    attempt_count, sent_at, last_error, payload_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'PENDING', ?, 0, NULL, NULL, ?, ?, ?)
                ON CONFLICT(outbox_id) DO UPDATE SET
                    signal_id = excluded.signal_id,
                    channel = excluded.channel,
                    event_type = excluded.event_type,
                    status = CASE
                        WHEN notification_outbox.status = 'SENT' THEN notification_outbox.status
                        ELSE 'PENDING'
                    END,
                    available_at = excluded.available_at,
                    last_error = CASE
                        WHEN notification_outbox.status = 'SENT' THEN notification_outbox.last_error
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

    def mark_notification_sent(self, outbox_id: str) -> None:
        now = _now_text()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE notification_outbox
                SET status = 'SENT',
                    sent_at = ?,
                    last_error = NULL,
                    attempt_count = attempt_count + CASE WHEN status != 'SENT' THEN 1 ELSE 0 END,
                    updated_at = ?
                WHERE outbox_id = ?
                """,
                (now, now, outbox_id),
            )

    def mark_notification_failed(self, outbox_id: str, error: str) -> None:
        now = _now_text()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE notification_outbox
                SET status = 'FAILED',
                    last_error = ?,
                    attempt_count = attempt_count + 1,
                    updated_at = ?
                WHERE outbox_id = ?
                """,
                (error[:1000], now, outbox_id),
            )

    def pending_notifications(self, *, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM notification_outbox
                WHERE status IN ('PENDING', 'FAILED')
                ORDER BY available_at, created_at
                LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
        return [self._outbox_row(row) for row in rows]

    def outbox_summary(self) -> dict[str, Any]:
        with self._connect() as conn:
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
            "attempt_count": row["attempt_count"],
            "sent_at": row["sent_at"],
            "last_error": row["last_error"],
            "payload": payload,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def summary(self) -> dict[str, Any]:
        counts = Counter(item.status for item in self.records)
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
            "triggered": counts.get("TRIGGERED", 0),
            "confirmed": counts.get("CONFIRMED", 0),
            "invalidated": counts.get("INVALIDATED", 0),
            "expired": counts.get("EXPIRED", 0),
            "target_reached": counts.get("TARGET_REACHED", 0),
            "stop_reached": counts.get("STOP_REACHED", 0),
            "timeout_result": counts.get("TIMEOUT_RESULT", 0),
            "active": counts.get("TRIGGERED", 0) + counts.get("CONFIRMED", 0),
            "terminal": (
                counts.get("INVALIDATED", 0)
                + counts.get("EXPIRED", 0)
                + counts.get("TARGET_REACHED", 0)
                + counts.get("STOP_REACHED", 0)
                + counts.get("TIMEOUT_RESULT", 0)
            ),
            "latest_event_type": latest_event_type,
            "latest_event_at": latest_event_at,
            "outbox": outbox,
            "updated_at": latest_updated or _now_text(),
        }

    def get(self, signal_id: str) -> SignalLifecycleRecord | None:
        return self._by_id.get(signal_id)

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
        return df.dropna(subset=["ts", "close"]).sort_values("ts").reset_index(drop=True)

    def _update_record(
        self,
        record: SignalLifecycleRecord,
        df: pd.DataFrame,
        lifecycle_events: list[tuple[SignalLifecycleRecord, str, str, str]] | None = None,
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
            if record.bars_seen != bars_seen or record.last_closed_time != closed_time or record.last_close != close:
                record.bars_seen = bars_seen
                record.last_closed_time = closed_time
                record.last_close = close
                changed = True

            if record.status == "TRIGGERED" and self._invalidates(record, close):
                record.status = "INVALIDATED"
                record.invalidated_at = closed_time
                record.last_event_type = "INVALIDATED"
                record.last_event_at = closed_time
                record.updated_at = _now_text()
                if lifecycle_events is not None:
                    lifecycle_events.append((record, "INVALIDATED", closed_time, record.status))
                return True

            if record.status == "TRIGGERED" and self._confirms(record, close):
                record.status = "CONFIRMED"
                record.confirmed_at = closed_time
                record.last_event_type = "CONFIRMED"
                record.last_event_at = closed_time
                changed = True
                if lifecycle_events is not None:
                    lifecycle_events.append((record, "CONFIRMED", closed_time, record.status))

            if record.status == "CONFIRMED":
                result_event = self._confirmed_result_event(record, close)
                if result_event is not None:
                    event_type, attr_name = result_event
                    record.status = event_type
                    setattr(record, attr_name, closed_time)
                    record.last_event_type = event_type
                    record.last_event_at = closed_time
                    record.updated_at = _now_text()
                    if lifecycle_events is not None:
                        lifecycle_events.append((record, event_type, closed_time, record.status))
                    return True
                if record.max_hold_bars > 0 and bars_seen >= record.max_hold_bars:
                    record.status = "TIMEOUT_RESULT"
                    record.timeout_result_at = closed_time
                    record.last_event_type = "TIMEOUT_RESULT"
                    record.last_event_at = closed_time
                    record.updated_at = _now_text()
                    if lifecycle_events is not None:
                        lifecycle_events.append((record, "TIMEOUT_RESULT", closed_time, record.status))
                    return True

            if record.status == "TRIGGERED" and record.max_hold_bars > 0 and bars_seen >= record.max_hold_bars:
                record.status = "EXPIRED"
                record.expired_at = closed_time
                record.last_event_type = "EXPIRED"
                record.last_event_at = closed_time
                record.updated_at = _now_text()
                if lifecycle_events is not None:
                    lifecycle_events.append((record, "EXPIRED", closed_time, record.status))
                return True

        if changed:
            record.updated_at = _now_text()
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
    def _target_reached(record: SignalLifecycleRecord, close: float) -> bool:
        if record.take_profit is None:
            return False
        if record.side == "long":
            return close >= record.take_profit
        return close <= record.take_profit

    @staticmethod
    def _stop_reached(record: SignalLifecycleRecord, close: float) -> bool:
        if record.side == "long":
            return close <= record.invalidation_price
        return close >= record.invalidation_price

    def _confirmed_result_event(self, record: SignalLifecycleRecord, close: float) -> tuple[str, str] | None:
        if self._target_reached(record, close):
            return "TARGET_REACHED", "target_reached_at"
        if self._stop_reached(record, close):
            return "STOP_REACHED", "stop_reached_at"
        return None


__all__ = [
    "LifecycleStatus",
    "SignalLifecycleRecord",
    "SignalLifecycleStore",
    "lifecycle_payload",
]
