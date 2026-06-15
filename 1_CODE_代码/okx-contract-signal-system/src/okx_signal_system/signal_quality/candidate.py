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

    @property
    def inst_id(self) -> str:
        return str(getattr(self.signal, "inst_id", self.health_item.get("symbol", "")))

    @property
    def side(self) -> str:
        return str(getattr(self.signal, "side", ""))

    @property
    def candle_time(self) -> pd.Timestamp:
        return pd.Timestamp(getattr(self.signal, "ts"))
