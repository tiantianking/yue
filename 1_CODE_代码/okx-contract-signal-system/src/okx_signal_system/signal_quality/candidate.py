from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class SignalCandidate:
    signal: Any
    decision: Any
    notify_key: str
    payload: dict
    health_item: dict
    rank_score: float
    raw_score: float
    tier: str | None = None
    rank: int | None = None
    correlation_group: str | None = None

    @property
    def inst_id(self) -> str:
        return str(getattr(self.signal, "inst_id", self.health_item.get("symbol", "")))

    @property
    def side(self) -> str:
        return str(getattr(self.signal, "side", ""))

    @property
    def invalidation_price(self) -> float | None:
        value = getattr(self.signal, "stop_loss", None)
        return float(value) if value is not None else None

    @property
    def candle_time(self) -> pd.Timestamp:
        return pd.Timestamp(getattr(self.signal, "ts"))


@dataclass(frozen=True)
class ObservationCandidate:
    inst_id: str
    side: str
    candle_time: pd.Timestamp
    close: float
    breakout_level: float
    breakout_gap_pct: float
    payload: dict
    health_item: dict
    rank_score: float
    raw_score: float
    tier: str | None = None
    rank: int | None = None
    correlation_group: str | None = None

    @property
    def invalidation_price(self) -> None:
        return None


CandidateLike = SignalCandidate | ObservationCandidate
