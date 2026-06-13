from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from okx_signal_system.config import project_paths
from okx_signal_system.risk.model import RiskDecision
from okx_signal_system.strategy.trend_breakout import TradeSignal


@dataclass
class ShadowSignal:
    signal_id: str
    inst_id: str
    side: str
    signal_time: str
    entry_ref: float
    stop_loss: float
    take_profit: float
    max_hold_bars: int
    signal_score: float
    risk_reward_ratio: float
    leverage_used: float
    qty: float
    status: str = "open"
    bars_seen: int = 0
    max_favorable_r: float = 0.0
    max_adverse_r: float = 0.0
    exit_time: str | None = None
    exit_price: float | None = None
    outcome: str | None = None
    realized_r: float | None = None
    quality_score: float | None = None


def _signal_id(signal: TradeSignal) -> str:
    ts = pd.Timestamp(signal.ts).isoformat()
    return f"{signal.inst_id}:{signal.side}:{ts}:{float(signal.entry_ref or 0):.8f}"


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


class ShadowTradingLedger:
    """Paper-review ledger for signals that were actually allowed to push."""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else project_paths().output_dir / "shadow_signals.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.signals: list[ShadowSignal] = []
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.signals = [ShadowSignal(**item) for item in data if isinstance(item, dict)]
        except Exception:
            self.signals = []

    def _save(self) -> None:
        payload = [asdict(item) for item in self.signals[-1000:]]
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def record_signal(self, signal: TradeSignal, decision: RiskDecision) -> bool:
        if not signal.accepted or signal.entry_ref is None or signal.stop_loss is None or signal.take_profit is None:
            return False
        sid = _signal_id(signal)
        if any(item.signal_id == sid for item in self.signals):
            return False
        self.signals.append(
            ShadowSignal(
                signal_id=sid,
                inst_id=signal.inst_id,
                side=signal.side,
                signal_time=pd.Timestamp(signal.ts).isoformat(),
                entry_ref=float(signal.entry_ref),
                stop_loss=float(signal.stop_loss),
                take_profit=float(signal.take_profit),
                max_hold_bars=int(signal.max_hold_bars or 0),
                signal_score=float(decision.signal_score or signal.signal_score or 0.0),
                risk_reward_ratio=float(decision.risk_reward_ratio or signal.risk_reward_ratio or 0.0),
                leverage_used=float(decision.leverage_used or 0.0),
                qty=float(decision.qty or 0.0),
            )
        )
        self._save()
        return True

    def update_symbol(self, inst_id: str, frame: pd.DataFrame) -> int:
        if frame.empty or "ts" not in frame.columns:
            return 0
        df = frame.sort_values("ts").reset_index(drop=True).copy()
        df["ts"] = pd.to_datetime(df["ts"], utc=True)
        updated = 0
        for item in self.signals:
            if item.inst_id != inst_id or item.status != "open":
                continue
            start = pd.Timestamp(item.signal_time)
            if start.tzinfo is None:
                start = start.tz_localize("UTC")
            future = df[df["ts"] > start].reset_index(drop=True)
            if future.empty:
                continue
            stop_dist = abs(item.entry_ref - item.stop_loss)
            if stop_dist <= 0:
                continue
            item.bars_seen = int(len(future))
            if item.side == "long":
                item.max_favorable_r = max(item.max_favorable_r, float((future["high"].max() - item.entry_ref) / stop_dist))
                item.max_adverse_r = min(item.max_adverse_r, float((future["low"].min() - item.entry_ref) / stop_dist))
            else:
                item.max_favorable_r = max(item.max_favorable_r, float((item.entry_ref - future["low"].min()) / stop_dist))
                item.max_adverse_r = min(item.max_adverse_r, float((item.entry_ref - future["high"].max()) / stop_dist))

            exit_time = None
            exit_price = None
            outcome = None
            for _, row in future.iterrows():
                if item.side == "long":
                    if float(row["low"]) <= item.stop_loss:
                        exit_time, exit_price, outcome = row["ts"], item.stop_loss, "stop_loss"
                        break
                    if float(row["high"]) >= item.take_profit:
                        exit_time, exit_price, outcome = row["ts"], item.take_profit, "take_profit"
                        break
                else:
                    if float(row["high"]) >= item.stop_loss:
                        exit_time, exit_price, outcome = row["ts"], item.stop_loss, "stop_loss"
                        break
                    if float(row["low"]) <= item.take_profit:
                        exit_time, exit_price, outcome = row["ts"], item.take_profit, "take_profit"
                        break
            if outcome is None and item.max_hold_bars > 0 and len(future) >= item.max_hold_bars:
                row = future.iloc[item.max_hold_bars - 1]
                exit_time, exit_price, outcome = row["ts"], float(row["close"]), "max_hold"

            if outcome is None:
                updated += 1
                continue

            side_mult = 1.0 if item.side == "long" else -1.0
            realized_r = ((float(exit_price) - item.entry_ref) * side_mult) / stop_dist
            item.status = "closed"
            item.exit_time = pd.Timestamp(exit_time).isoformat()
            item.exit_price = float(exit_price)
            item.outcome = outcome
            item.realized_r = float(realized_r)
            if outcome == "take_profit":
                item.quality_score = 100.0
            elif outcome == "stop_loss":
                item.quality_score = 0.0
            else:
                item.quality_score = _clamp(50.0 + realized_r * 12.5, 0.0, 100.0)
            updated += 1
        if updated:
            self._save()
        return updated

    def score_adjustment(self, inst_id: str, side: str, *, min_closed: int = 6) -> float:
        closed = [
            item
            for item in self.signals
            if item.inst_id == inst_id
            and item.side == side
            and item.status == "closed"
            and item.quality_score is not None
        ][-20:]
        if len(closed) < min_closed:
            return 0.0
        avg = sum(float(item.quality_score or 0.0) for item in closed) / len(closed)
        if avg >= 80:
            return 0.7
        if avg >= 70:
            return 0.4
        if avg < 45:
            return -1.0
        if avg < 55:
            return -0.5
        return 0.0

    def summary(self) -> dict:
        closed = [item for item in self.signals if item.status == "closed" and item.quality_score is not None]
        open_count = sum(1 for item in self.signals if item.status == "open")
        avg_score = sum(float(item.quality_score or 0.0) for item in closed) / len(closed) if closed else 0.0
        tp_count = sum(1 for item in closed if item.outcome == "take_profit")
        sl_count = sum(1 for item in closed if item.outcome == "stop_loss")
        return {
            "open": open_count,
            "closed": len(closed),
            "take_profit": tp_count,
            "stop_loss": sl_count,
            "avg_quality_score": avg_score,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
