"""Signal outcome records and stop/target observation."""
from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Literal

import pandas as pd

from okx_signal_system.config import project_paths
from okx_signal_system.risk.costs import CostConfig, estimate_costs

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class PositionRecord:
    inst_id: str
    side: Literal["long", "short"]
    entry_price: float
    size: float
    stop_loss: float
    take_profit: float
    leverage: float
    entry_time: str
    signal_score: float | None = None
    risk_reward_ratio: float | None = None
    margin_mode: str = "isolated"

    @property
    def key(self) -> str:
        return f"{self.inst_id}_{self.side}"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "PositionRecord":
        fields = cls.__dataclass_fields__
        return cls(**{k: v for k, v in data.items() if k in fields})


@dataclass(frozen=True)
class CloseResult:
    inst_id: str
    side: str
    entry_price: float
    exit_price: float
    size: float
    exit_reason: str
    gross_pnl: float
    entry_fee: float
    exit_fee: float
    slippage_cost: float
    funding_fee: float
    total_costs: float
    net_pnl: float
    net_pnl_pct: float
    close_time: str
    signal_score: float | None = None


def _records_dir() -> Path:
    return project_paths().output_dir / "position_records"


def _safe_key(key: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in key)


def _parse_time(value: str) -> pd.Timestamp:
    try:
        ts = pd.Timestamp(value)
    except Exception:
        ts = pd.Timestamp.now(tz="UTC")
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    return ts.tz_convert("UTC")


def validate_position_record(record: PositionRecord) -> None:
    if record.side not in {"long", "short"}:
        raise ValueError("side must be long or short")
    for name in ("entry_price", "size", "stop_loss", "take_profit", "leverage"):
        if getattr(record, name) <= 0:
            raise ValueError(f"{name} must be positive")
    if record.side == "long" and not (record.stop_loss < record.entry_price < record.take_profit):
        raise ValueError("long position requires stop_loss < entry_price < take_profit")
    if record.side == "short" and not (record.take_profit < record.entry_price < record.stop_loss):
        raise ValueError("short position requires take_profit < entry_price < stop_loss")


class PositionRecordStore:
    def __init__(self, directory: Path | str | None = None):
        self.directory = Path(directory) if directory else _records_dir()
        self.directory.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        return self.directory / f"{_safe_key(key)}.json"

    def save(self, record: PositionRecord) -> None:
        validate_position_record(record)
        self._path(record.key).write_text(
            json.dumps(record.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def load(self, key: str) -> PositionRecord | None:
        path = self._path(key)
        if not path.exists():
            return None
        try:
            return PositionRecord.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except Exception as exc:
            log.warning("Failed to load position record %s: %s", key, exc)
            return None

    def load_all(self) -> dict[str, PositionRecord]:
        records: dict[str, PositionRecord] = {}
        for path in self.directory.glob("*.json"):
            try:
                record = PositionRecord.from_dict(json.loads(path.read_text(encoding="utf-8")))
                validate_position_record(record)
                records[record.key] = record
            except Exception as exc:
                log.warning("Skipping invalid position record %s: %s", path.name, exc)
        return records

    def delete(self, key: str) -> None:
        path = self._path(key)
        if path.exists():
            path.unlink()


class AutoStopMonitor:
    """Poll recorded signal outcomes and report stop/target triggers.

    This SIGNAL_ONLY runtime never submits close orders. The monitor exists only
    to observe whether a manually reviewed signal reached its analysis stop or
    target after being recorded.
    """

    def __init__(
        self,
        check_interval: float = 5.0,
        trigger_buffer_pct: float = 0.0,
        cost_config: CostConfig | None = None,
        store: PositionRecordStore | None = None,
        auto_close_enabled: bool | None = None,
    ):
        self.check_interval = check_interval
        self.trigger_buffer_pct = trigger_buffer_pct
        self.cost_config = cost_config or CostConfig()
        self.record_store = store or PositionRecordStore()
        self.auto_close_enabled = False
        self._running = False
        self._thread: threading.Thread | None = None
        self._on_close_callback: Callable[[CloseResult], None] | None = None
        self._notified_triggers: set[tuple[str, str]] = set()

    def set_on_close_callback(self, callback: Callable[[CloseResult], None]) -> None:
        self._on_close_callback = callback

    def register_position(self, record: PositionRecord) -> None:
        self.record_store.save(record)

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=max(1.0, self.check_interval + 1.0))

    def get_active_positions(self) -> list[PositionRecord]:
        return list(self.record_store.load_all().values())

    def remove_position(self, key: str) -> None:
        self.record_store.delete(key)

    def _monitor_loop(self) -> None:
        while self._running:
            try:
                self._check_all_positions()
            except Exception as exc:
                log.warning("Auto stop monitor cycle failed: %s", exc)
            time.sleep(self.check_interval)

    def _check_all_positions(self) -> None:
        for record in self.record_store.load_all().values():
            current_price = self._latest_price(record.inst_id)
            if current_price is None:
                continue
            should_close, reason = self._check_price(record, current_price)
            if should_close:
                self._handle_trigger(record, current_price, reason)

    def _latest_price(self, inst_id: str) -> float | None:
        try:
            from okx_signal_system.exchange.okx import get_ticker

            ticker = get_ticker(inst_id)
            return float(ticker["last"])
        except Exception as exc:
            log.debug("Ticker unavailable for %s: %s", inst_id, exc)
            return None

    def _check_price(self, record: PositionRecord, current_price: float) -> tuple[bool, str]:
        buffer = current_price * self.trigger_buffer_pct
        if record.side == "long":
            if current_price <= record.stop_loss + buffer:
                return True, "stop_loss"
            if current_price >= record.take_profit - buffer:
                return True, "take_profit"
        else:
            if current_price >= record.stop_loss - buffer:
                return True, "stop_loss"
            if current_price <= record.take_profit + buffer:
                return True, "take_profit"
        return False, ""

    def _handle_trigger(self, record: PositionRecord, current_price: float, reason: str) -> None:
        marker = (record.key, reason)
        if marker in self._notified_triggers:
            return
        self._notified_triggers.add(marker)

        result = self._build_close_result(record, current_price, reason)
        log.warning(
            "Signal outcome trigger observed; %s hit %s at %.8f",
            record.key,
            reason,
            current_price,
        )
        if self._on_close_callback:
            self._on_close_callback(result)
        self._send_close_notification(result)

    def _build_close_result(self, record: PositionRecord, exit_price: float, reason: str) -> CloseResult:
        side_mult = 1.0 if record.side == "long" else -1.0
        gross_pnl = (exit_price - record.entry_price) * record.size * side_mult
        exit_time = datetime.now(timezone.utc)
        costs = estimate_costs(
            entry_price=record.entry_price,
            exit_price=exit_price,
            qty=record.size,
            entry_time=_parse_time(record.entry_time),
            exit_time=pd.Timestamp(exit_time),
            config=self.cost_config,
        )
        net_pnl = gross_pnl - costs.total
        margin = record.entry_price * record.size / record.leverage
        return CloseResult(
            inst_id=record.inst_id,
            side=record.side,
            entry_price=record.entry_price,
            exit_price=exit_price,
            size=record.size,
            exit_reason=reason,
            gross_pnl=gross_pnl,
            entry_fee=costs.entry_fee,
            exit_fee=costs.exit_fee,
            slippage_cost=costs.slippage_cost,
            funding_fee=costs.funding_fee,
            total_costs=costs.total,
            net_pnl=net_pnl,
            net_pnl_pct=net_pnl / margin if margin > 0 else 0.0,
            close_time=exit_time.isoformat(),
            signal_score=record.signal_score,
        )

    def _send_close_notification(self, result: CloseResult) -> None:
        try:
            from okx_signal_system.notify.feishu import send_close_notification

            send_close_notification(
                inst_id=result.inst_id,
                side=result.side,
                entry_price=result.entry_price,
                exit_price=result.exit_price,
                size=result.size,
                exit_reason=result.exit_reason,
                gross_pnl=result.gross_pnl,
                net_pnl=result.net_pnl,
                net_pnl_pct=result.net_pnl_pct,
                entry_fee=result.entry_fee,
                exit_fee=result.exit_fee,
                slippage_cost=result.slippage_cost,
                funding_fee=result.funding_fee,
                total_costs=result.total_costs,
                signal_score=result.signal_score,
            )
        except Exception as exc:
            log.debug("Close notification skipped: %s", exc)


def register_manual_position(
    inst_id: str,
    side: str,
    entry_price: float,
    size: float,
    stop_loss: float,
    take_profit: float,
    leverage: float,
    signal_score: float | None = None,
    risk_reward_ratio: float | None = None,
) -> PositionRecord:
    record = PositionRecord(
        inst_id=inst_id,
        side=side,  # type: ignore[arg-type]
        entry_price=entry_price,
        size=size,
        stop_loss=stop_loss,
        take_profit=take_profit,
        leverage=leverage,
        entry_time=datetime.now(timezone.utc).isoformat(),
        signal_score=signal_score,
        risk_reward_ratio=risk_reward_ratio,
    )
    PositionRecordStore().save(record)
    return record
