from __future__ import annotations

import json
import os
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import pandas as pd

from okx_signal_system.config import project_paths

LifecycleStatus = Literal["TRIGGERED", "CONFIRMED", "INVALIDATED", "EXPIRED"]


@dataclass
class SignalLifecycleRecord:
    signal_id: str
    inst_id: str
    side: str
    signal_time: str
    entry_ref: float
    invalidation_price: float
    max_hold_bars: int
    status: LifecycleStatus = "TRIGGERED"
    bars_seen: int = 0
    last_closed_time: str | None = None
    last_close: float | None = None
    confirmed_at: str | None = None
    invalidated_at: str | None = None
    expired_at: str | None = None
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
        "status": record.status,
        "triggered_at": record.signal_time,
        "invalidation_price": record.invalidation_price,
        "bars_seen": record.bars_seen,
        "last_closed_time": record.last_closed_time,
        "last_close": record.last_close,
        "confirmed_at": record.confirmed_at,
        "invalidated_at": record.invalidated_at,
        "expired_at": record.expired_at,
    }


class SignalLifecycleStore:
    """Persist and update signal lifecycle states using closed candles only."""

    def __init__(self, path: str | Path | None = None, *, max_records: int = 1000):
        self.path = Path(path) if path else project_paths().output_dir / "signal_lifecycle.json"
        self.max_records = max_records
        self.records: list[SignalLifecycleRecord] = []
        self._by_id: dict[str, SignalLifecycleRecord] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            self.records = []
            self._by_id = {}
            return
        self.records = [
            SignalLifecycleRecord(**item)
            for item in data
            if isinstance(item, dict) and item.get("signal_id")
        ]
        self._by_id = {item.signal_id: item for item in self.records}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.records = self.records[-self.max_records :]
        self._by_id = {item.signal_id: item for item in self.records}
        tmp_path = self.path.with_name(f"{self.path.stem}.{os.getpid()}.tmp{self.path.suffix}")
        payload = [asdict(item) for item in self.records]
        tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_path.replace(self.path)

    def record_signal(
        self,
        signal: Any,
        *,
        signal_id: str | None = None,
        invalidation_price: float | None = None,
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
        record = SignalLifecycleRecord(
            signal_id=sid,
            inst_id=str(getattr(signal, "inst_id", "")),
            side=side,
            signal_time=_timestamp_text(getattr(signal, "ts")),
            entry_ref=float(entry_ref),
            invalidation_price=float(invalidation),
            max_hold_bars=int(getattr(signal, "max_hold_bars", 0) or 0),
            signal_timeframe=signal_timeframe,
            trend_timeframe=trend_timeframe,
            created_at=now,
            updated_at=now,
        )
        self.records.append(record)
        self._by_id[sid] = record
        self._save()
        return record

    def update_symbol(self, inst_id: str, frame: pd.DataFrame) -> int:
        df = self._closed_frame(frame)
        if df.empty:
            return 0
        updated = 0
        for record in self.records:
            if record.inst_id != inst_id or record.status not in {"TRIGGERED", "CONFIRMED"}:
                continue
            if self._update_record(record, df):
                updated += 1
        if updated:
            self._save()
        return updated

    def summary(self) -> dict[str, Any]:
        counts = Counter(item.status for item in self.records)
        return {
            "total": len(self.records),
            "triggered": counts.get("TRIGGERED", 0),
            "confirmed": counts.get("CONFIRMED", 0),
            "invalidated": counts.get("INVALIDATED", 0),
            "expired": counts.get("EXPIRED", 0),
            "updated_at": _now_text(),
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

    def _update_record(self, record: SignalLifecycleRecord, df: pd.DataFrame) -> bool:
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

            if self._invalidates(record, close):
                record.status = "INVALIDATED"
                record.invalidated_at = closed_time
                record.updated_at = _now_text()
                return True

            if record.status == "TRIGGERED" and self._confirms(record, close):
                record.status = "CONFIRMED"
                record.confirmed_at = closed_time
                changed = True

            if record.max_hold_bars > 0 and bars_seen >= record.max_hold_bars:
                record.status = "EXPIRED"
                record.expired_at = closed_time
                record.updated_at = _now_text()
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


__all__ = [
    "LifecycleStatus",
    "SignalLifecycleRecord",
    "SignalLifecycleStore",
    "lifecycle_payload",
]
