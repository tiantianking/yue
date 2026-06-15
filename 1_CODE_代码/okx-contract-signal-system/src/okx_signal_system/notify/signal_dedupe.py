"""Persistent de-duplication for signal notifications."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


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
) -> str:
    """Build a stable key for one tradable signal on one K-line."""
    return "|".join(
        [
            str(getattr(signal, "inst_id", "")),
            str(getattr(signal, "side", "")),
            _timestamp_text(getattr(signal, "ts", "")),
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

                path = project_paths().output_dir / "signal_notifications.json"
            except Exception:
                path = Path("outputs") / "signal_notifications.json"
        self.path = path
        self.max_records = max_records
        self._records: list[dict[str, Any]] = []
        self._keys: set[str] = set()
        self._load()

    def _load(self) -> None:
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
        self._load()
        if key in self._keys:
            return False

        record = {
            "key": key,
            "notified_at": datetime.now(timezone.utc).isoformat(),
            **(metadata or {}),
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
