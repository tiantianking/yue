"""Persistent de-duplication for signal notifications."""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from okx_signal_system.signal_runtime import parameter_hash, signal_id, strategy_version


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if hasattr(value, "item"):
        return value.item()
    return str(value)


def _timestamp_text(value: Any) -> str:
    if value is None:
        return ""
    if hasattr(value, "isoformat"):
        return str(value.isoformat())
    return str(value)


def signal_notification_key(
    signal: Any,
    *,
    signal_timeframe: str | None = None,
    trend_timeframe: str | None = None,
    params: Any | None = None,
) -> str:
    """Build a stable key for one tradable signal on one K-line."""
    if params is not None:
        return signal_id(signal, params)
    return "|".join(
        [
            str(getattr(signal, "inst_id", "")),
            str(getattr(signal, "side", "")),
            _timestamp_text(getattr(signal, "ts", "")),
            signal_timeframe or "",
            trend_timeframe or "",
        ]
    )


def b_tier_summary_key(
    candle_time: Any,
    *,
    signal_timeframe: str | None = None,
    trend_timeframe: str | None = None,
) -> str:
    """Build a separate key for one B-tier summary on one K-line."""
    return "|".join(
        [
            "b_tier_summary",
            _timestamp_text(candle_time),
            signal_timeframe or "",
            trend_timeframe or "",
        ]
    )


class SignalNotificationStore:
    """Remember which trade signals have already produced an external alert."""

    def __init__(self, path: Path | None = None, *, max_records: int = 1000):
        if path is None:
            try:
                from okx_signal_system.config import project_paths

                path = project_paths().output_dir / "pushed_signals.sqlite3"
            except Exception:
                path = Path("outputs") / "pushed_signals.sqlite3"
        self.path = path
        self.max_records = max_records
        self._records: list[dict[str, Any]] = []
        self._keys: set[str] = set()
        self._sqlite = self.path.suffix.lower() in {".sqlite", ".sqlite3", ".db"}
        self._load()

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pushed_signals (
                signal_id TEXT PRIMARY KEY,
                inst_id TEXT NOT NULL,
                candle_time TEXT NOT NULL,
                side TEXT NOT NULL,
                tier TEXT NOT NULL,
                pushed_at TEXT NOT NULL,
                payload_json TEXT NOT NULL
            )
            """
        )
        return conn

    def _load(self) -> None:
        if self._sqlite:
            with self._connect() as conn:
                rows = conn.execute("SELECT signal_id FROM pushed_signals").fetchall()
            self._keys = {str(row[0]) for row in rows}
            self._records = []
            return
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            self._records = []
            self._keys = set()
            return

        records = data.get("signals", data) if isinstance(data, dict) else data
        if not isinstance(records, list):
            records = []
        self._records = [item for item in records if isinstance(item, dict) and item.get("key")]
        self._keys = {str(item["key"]) for item in self._records}

    def has(self, key: str) -> bool:
        self._load()
        return key in self._keys

    def mark(self, key: str, metadata: dict[str, Any] | None = None) -> bool:
        """Persist a successful notification. Returns False when already marked."""
        metadata = metadata or {}
        if self._sqlite:
            payload = {
                "key": key,
                "notified_at": datetime.now(timezone.utc).isoformat(),
                **metadata,
            }
            try:
                with self._connect() as conn:
                    conn.execute(
                        """
                        INSERT INTO pushed_signals (
                            signal_id, inst_id, candle_time, side, tier, pushed_at, payload_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            key,
                            str(metadata.get("symbol") or metadata.get("inst_id") or ""),
                            str(metadata.get("kline_time") or metadata.get("candle_time") or ""),
                            str(metadata.get("side") or ""),
                            str(metadata.get("tier") or "A"),
                            datetime.now(timezone.utc).isoformat(),
                            json.dumps(payload, ensure_ascii=False, default=_json_default),
                        ),
                    )
                self._keys.add(key)
                return True
            except sqlite3.IntegrityError:
                self._keys.add(key)
                return False

        self._load()
        if key in self._keys:
            return False

        record = {
            "key": key,
            "notified_at": datetime.now(timezone.utc).isoformat(),
            **metadata,
        }
        self._records.append(record)
        if len(self._records) > self.max_records:
            self._records = self._records[-self.max_records :]
        self._keys = {str(item["key"]) for item in self._records}
        self._save()
        return True

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_name(f"{self.path.stem}.{os.getpid()}.tmp{self.path.suffix}")
        payload = {"signals": self._records}
        tmp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default),
            encoding="utf-8",
        )
        tmp_path.replace(self.path)


class BTierSummaryNotificationStore:
    """Remember which B-tier summaries have already been pushed."""

    def __init__(self, path: Path | None = None):
        if path is None:
            try:
                from okx_signal_system.config import project_paths

                path = project_paths().output_dir / "pushed_b_tier_summaries.sqlite3"
            except Exception:
                path = Path("outputs") / "pushed_b_tier_summaries.sqlite3"
        self.path = path

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pushed_b_tier_summaries (
                summary_id TEXT PRIMARY KEY,
                candle_time TEXT NOT NULL,
                pushed_at TEXT NOT NULL,
                payload_json TEXT NOT NULL
            )
            """
        )
        return conn

    def has(self, key: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM pushed_b_tier_summaries WHERE summary_id = ?",
                (key,),
            ).fetchone()
        return row is not None

    def mark(self, key: str, metadata: dict[str, Any] | None = None) -> bool:
        metadata = metadata or {}
        payload = {
            "key": key,
            "notified_at": datetime.now(timezone.utc).isoformat(),
            **metadata,
        }
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO pushed_b_tier_summaries (
                        summary_id, candle_time, pushed_at, payload_json
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (
                        key,
                        str(metadata.get("kline_time") or metadata.get("candle_time") or ""),
                        datetime.now(timezone.utc).isoformat(),
                        json.dumps(payload, ensure_ascii=False, default=_json_default),
                    ),
                )
            return True
        except sqlite3.IntegrityError:
            return False


__all__ = [
    "BTierSummaryNotificationStore",
    "SignalNotificationStore",
    "b_tier_summary_key",
    "parameter_hash",
    "signal_id",
    "signal_notification_key",
    "strategy_version",
]
