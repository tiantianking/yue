"""Lightweight trade pattern tracking."""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from okx_signal_system.paths import find_lightweight_history

log = logging.getLogger(__name__)

MIN_TRADES_FOR_ANALYSIS = 50


@dataclass(frozen=True)
class TradeContext:
    inst_id: str
    side: str
    entry_price: float
    exit_price: float
    net_pnl: float
    exit_reason: str
    signal_score: float | None
    hour_utc: int
    day_of_week: int
    market_regime: str = "unknown"
    leverage: float = 0.0
    timestamp: str = ""


@dataclass(frozen=True)
class DiscoveredPattern:
    condition: str
    metric: str
    value: float
    sample_size: int
    confidence: float
    recommendation: str
    is_positive: bool


class PatternRecognizer:
    def __init__(self, data_dir: Path | str | None = None):
        if data_dir is None:
            data_dir = find_lightweight_history("okx_15m_extended").parent / "pattern_recognition"
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._contexts: list[TradeContext] = []
        self._patterns: list[DiscoveredPattern] = []
        self._load()

    @property
    def contexts_file(self) -> Path:
        return self.data_dir / "trade_contexts.json"

    @property
    def patterns_file(self) -> Path:
        return self.data_dir / "discovered_patterns.json"

    def _load(self) -> None:
        if self.contexts_file.exists():
            try:
                data = json.loads(self.contexts_file.read_text(encoding="utf-8"))
                self._contexts = [TradeContext(**item) for item in data]
            except Exception as exc:
                log.warning("Failed to load trade contexts: %s", exc)
        if self.patterns_file.exists():
            try:
                data = json.loads(self.patterns_file.read_text(encoding="utf-8"))
                self._patterns = [DiscoveredPattern(**item) for item in data]
            except Exception as exc:
                log.warning("Failed to load patterns: %s", exc)

    def _save(self) -> None:
        self.contexts_file.write_text(
            json.dumps([asdict(c) for c in self._contexts[-500:]], indent=2),
            encoding="utf-8",
        )
        self.patterns_file.write_text(
            json.dumps([asdict(p) for p in self._patterns[-100:]], indent=2),
            encoding="utf-8",
        )

    def record_trade_context(
        self,
        inst_id: str,
        side: str,
        entry_price: float,
        exit_price: float,
        net_pnl: float,
        exit_reason: str,
        signal_score: float | None = None,
        market_regime: str = "unknown",
        leverage: float = 0.0,
    ) -> None:
        now = datetime.now(timezone.utc)
        self._contexts.append(
            TradeContext(
                inst_id=inst_id,
                side=side,
                entry_price=entry_price,
                exit_price=exit_price,
                net_pnl=net_pnl,
                exit_reason=exit_reason,
                signal_score=signal_score,
                hour_utc=now.hour,
                day_of_week=now.weekday(),
                market_regime=market_regime,
                leverage=leverage,
                timestamp=now.isoformat(),
            )
        )
        if len(self._contexts) >= MIN_TRADES_FOR_ANALYSIS and len(self._contexts) % 10 == 0:
            self._patterns = self._analyze_patterns()
        self._save()

    def _analyze_patterns(self) -> list[DiscoveredPattern]:
        patterns: list[DiscoveredPattern] = []
        for label, selector in {
            "long": lambda c: c.side == "long",
            "short": lambda c: c.side == "short",
            "high_score": lambda c: c.signal_score is not None and c.signal_score >= 7,
            "low_score": lambda c: c.signal_score is not None and c.signal_score <= 4,
        }.items():
            group = [c for c in self._contexts if selector(c)]
            if len(group) < 5:
                continue
            win_rate = sum(1 for c in group if c.net_pnl > 0) / len(group)
            is_positive = win_rate >= 0.55
            confidence = min(len(group) / 30.0, 1.0)
            patterns.append(
                DiscoveredPattern(
                    condition=label,
                    metric="win_rate",
                    value=win_rate,
                    sample_size=len(group),
                    confidence=confidence,
                    recommendation=f"{label} win_rate={win_rate:.0%}",
                    is_positive=is_positive,
                )
            )
        return patterns

    def get_active_rules(self) -> list[dict]:
        return [
            {
                "action": "prefer" if pattern.is_positive else "avoid",
                "condition": pattern.condition,
                "reason": pattern.recommendation,
            }
            for pattern in self._patterns
            if pattern.confidence >= 0.5 and pattern.sample_size >= 5
        ]

    def get_score_adjustment(self, inst_id: str, side: str, hour_utc: int, regime: str) -> float:
        adjustment = 0.0
        for pattern in self._patterns:
            if pattern.confidence < 0.5:
                continue
            matched = (
                pattern.condition == side
                or pattern.condition in inst_id
                or pattern.condition in regime
                or pattern.condition == f"hour_{hour_utc}"
            )
            if matched:
                adjustment += 0.3 if pattern.is_positive else -0.5
        return adjustment

    def get_summary(self) -> dict:
        return {
            "total_trades": len(self._contexts),
            "patterns_found": len(self._patterns),
            "positive_patterns": sum(1 for p in self._patterns if p.is_positive),
            "negative_patterns": sum(1 for p in self._patterns if not p.is_positive),
            "active_rules": len(self.get_active_rules()),
        }
