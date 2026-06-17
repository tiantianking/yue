from __future__ import annotations

import logging
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Literal

import pandas as pd

from okx_signal_system.strategy.trend_breakout import StrategyParams

log = logging.getLogger(__name__)

RegimeType = Literal["high_vol_trend", "low_vol_trend", "high_vol_range", "low_vol_range", "unknown"]

REGIME_PARAMS: dict[str, StrategyParams] = {
    name: StrategyParams(
        fast_ema=120,
        slow_ema=720,
        breakout_window=384,
        atr_stop_mult=4.0,
        take_profit_mult=6.0,
        max_hold_bars=768,
        atr_window=14,
    )
    for name in ("high_vol_trend", "low_vol_trend", "high_vol_range", "low_vol_range")
}

REGIME_SCORE_PENALTY: dict[str, float] = {
    "high_vol_trend": 0.0,
    "low_vol_trend": 0.0,
    "high_vol_range": -1.0,
    "low_vol_range": -2.5,
    "unknown": -0.5,
}

REGIME_LEVERAGE_FACTOR: dict[str, float] = {
    "high_vol_trend": 1.0,
    "low_vol_trend": 0.9,
    "high_vol_range": 0.5,
    "low_vol_range": 0.3,
    "unknown": 0.7,
}


class RegimeDetector:
    @staticmethod
    def detect_from_features(features: pd.DataFrame) -> str:
        if len(features) < 20:
            return "unknown"

        last = features.iloc[-1]
        recent = features.iloc[-20:]
        try:
            atr_pct = float(last.get("atr_pct", 0)) if not pd.isna(last.get("atr_pct")) else 0.0
            ema_fast = float(last.get("ema_fast", 0)) if not pd.isna(last.get("ema_fast")) else 0.0
            ema_slow = float(last.get("ema_slow", 0)) if not pd.isna(last.get("ema_slow")) else 0.0
            vol_ratio = float(last.get("vol_ratio", 1.0)) if not pd.isna(last.get("vol_ratio")) else 1.0
            close = float(last.get("close", 0)) if not pd.isna(last.get("close")) else 0.0
        except (TypeError, ValueError):
            return "unknown"

        try:
            atr_col = recent.get("atr_pct", pd.Series([0.0] * 20))
            atr_avg = float(atr_col.mean()) if len(atr_col) > 0 else 0.0
            atr_ratio = atr_pct / atr_avg if atr_avg > 0 else 1.0
        except Exception:
            atr_ratio = 1.0

        ema_spread = 0.0
        if close > 0 and ema_slow > 0:
            ema_spread = (ema_fast - ema_slow) / close

        return RegimeDetector.classify(
            atr_pct=atr_pct,
            atr_avg_ratio=atr_ratio,
            ema_spread=ema_spread,
            volume_ratio=vol_ratio,
        )

    @staticmethod
    def classify(
        atr_pct: float,
        atr_avg_ratio: float,
        ema_spread: float,
        volume_ratio: float,
    ) -> str:
        is_high_vol = atr_avg_ratio > 1.2
        is_strong_trend = abs(ema_spread) > 0.005
        has_volume = volume_ratio > 0.8

        if is_high_vol and is_strong_trend and has_volume:
            return "high_vol_trend"
        if not is_high_vol and is_strong_trend:
            return "low_vol_trend"
        if is_high_vol and not is_strong_trend:
            return "high_vol_range"
        if not is_high_vol and not is_strong_trend:
            return "low_vol_range"
        return "unknown"


class AdaptiveParamsManager:
    """Runtime-only regime scorer.

    This preserves the realtime interface used by the scanner without importing
    the research/ML package tree or running any parameter promotion flow.
    """

    def __init__(self):
        self.current_regime: str = "unknown"
        self.current_params = StrategyParams()
        self._regime_history: list[dict] = []
        self._last_switch_time: datetime | None = None

    def update_regime(self, features: pd.DataFrame) -> tuple[str, StrategyParams]:
        new_regime = RegimeDetector.detect_from_features(features)

        if new_regime != self.current_regime:
            old_regime = self.current_regime
            self.current_regime = new_regime
            new_params = REGIME_PARAMS.get(new_regime, StrategyParams())

            if old_regime != "unknown" and self._last_switch_time is not None:
                elapsed = (datetime.now(timezone.utc) - self._last_switch_time).total_seconds()
                if elapsed < 3600:
                    log.debug("Regime %s -> %s switched recently; keeping current params", old_regime, new_regime)
                    return self.current_regime, self.current_params

            self.current_params = new_params
            self._last_switch_time = datetime.now(timezone.utc)
            self._regime_history.append(
                {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "old_regime": old_regime,
                    "new_regime": new_regime,
                    "params": asdict(new_params),
                }
            )

            log.info("market regime switched: %s -> %s", old_regime, new_regime)

        return self.current_regime, self.current_params

    def get_score_penalty(self) -> float:
        return 0.0

    def offline_score_penalty(self) -> float:
        return REGIME_SCORE_PENALTY.get(self.current_regime, 0.0)

    def get_leverage_factor(self) -> float:
        return 1.0

    def offline_leverage_factor(self) -> float:
        return REGIME_LEVERAGE_FACTOR.get(self.current_regime, 1.0)

    def get_regime_name_cn(self) -> str:
        names = {
            "high_vol_trend": "high_vol_trend",
            "low_vol_trend": "low_vol_trend",
            "high_vol_range": "high_vol_range",
            "low_vol_range": "low_vol_range",
            "unknown": "unknown",
        }
        return names.get(self.current_regime, "unknown")

    def get_regime_summary(self) -> dict:
        return {
            "current_regime": self.current_regime,
            "regime_name_cn": self.get_regime_name_cn(),
            "current_params": asdict(self.current_params),
            "score_penalty": self.get_score_penalty(),
            "observed_score_penalty": self.offline_score_penalty(),
            "leverage_factor": self.get_leverage_factor(),
            "observed_leverage_factor": self.offline_leverage_factor(),
            "switch_count": len(self._regime_history),
        }


__all__ = [
    "AdaptiveParamsManager",
    "RegimeDetector",
    "RegimeType",
]
