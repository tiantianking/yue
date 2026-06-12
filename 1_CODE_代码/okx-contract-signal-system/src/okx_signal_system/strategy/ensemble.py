"""Weighted strategy voting used by the desktop signal view."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
import pandas as pd

from okx_signal_system.strategy.trend_breakout import Side, StrategyParams, build_signal


@dataclass(frozen=True)
class StrategyVote:
    strategy_name: str
    vote: Literal["long", "short", "flat"]
    confidence: float
    weight: float
    reason: str = ""


@dataclass(frozen=True)
class EnsembleResult:
    final_side: Side
    final_score: float
    votes: list[StrategyVote]
    approval_rate: float
    details: str


def _bounded_confidence(value: float) -> float:
    if not np.isfinite(value):
        return 0.0
    return float(max(0.0, min(1.0, value)))


def _num(row: pd.Series, name: str, default: float = 0.0) -> float:
    value = row.get(name, default)
    if pd.isna(value):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _vote_trend_breakout(row: pd.Series, params: StrategyParams, frame: pd.DataFrame, idx: int) -> StrategyVote:
    signal = build_signal(row, inst_id="ENSEMBLE", params=params, frame=frame, idx=idx)
    if signal.accepted and signal.side in {"long", "short"}:
        close = _num(row, "close")
        ema_fast = _num(row, "ema_fast")
        ema_slow = _num(row, "ema_slow")
        strength = abs(ema_fast - ema_slow) / close if close > 0 else 0.0
        return StrategyVote(
            "trend_breakout",
            signal.side,
            _bounded_confidence(strength / 0.03),
            0.40,
            ",".join(signal.reason_codes),
        )
    return StrategyVote("trend_breakout", "flat", 0.0, 0.40, signal.reject_reason or "no_signal")


def _vote_mean_reversion(row: pd.Series) -> StrategyVote:
    close = _num(row, "close")
    ema_slow = _num(row, "ema_slow")
    atr_value = _num(row, "atr")
    if close <= 0 or ema_slow <= 0 or atr_value <= 0:
        return StrategyVote("mean_reversion", "flat", 0.0, 0.25, "insufficient_data")

    deviation = (close - ema_slow) / ema_slow
    threshold = max(atr_value / close * 2.0, 0.003)
    confidence = _bounded_confidence(abs(deviation) / (threshold * 2.0))
    if deviation < -threshold:
        return StrategyVote("mean_reversion", "long", confidence, 0.25, "oversold")
    if deviation > threshold:
        return StrategyVote("mean_reversion", "short", confidence, 0.25, "overbought")
    return StrategyVote("mean_reversion", "flat", 0.0, 0.25, "inside_band")


def _vote_momentum(row: pd.Series, frame: pd.DataFrame, idx: int) -> StrategyVote:
    if idx < 3 or frame.empty:
        return StrategyVote("momentum", "flat", 0.0, 0.20, "insufficient_data")

    recent = frame.iloc[max(0, idx - 3):idx + 1]
    if {"open", "close"}.difference(recent.columns):
        return StrategyVote("momentum", "flat", 0.0, 0.20, "missing_ohlc")

    moves = np.sign(recent["close"].astype(float).to_numpy() - recent["open"].astype(float).to_numpy())
    last_moves = moves[-3:]
    vol_ratio = _num(row, "vol_ratio", 1.0)
    volume_factor = _bounded_confidence(vol_ratio / 1.5)
    if np.all(last_moves > 0):
        return StrategyVote("momentum", "long", 0.6 + 0.4 * volume_factor, 0.20, "three_up")
    if np.all(last_moves < 0):
        return StrategyVote("momentum", "short", 0.6 + 0.4 * volume_factor, 0.20, "three_down")
    return StrategyVote("momentum", "flat", 0.0, 0.20, "mixed")


def _vote_volatility_breakout(row: pd.Series, frame: pd.DataFrame, idx: int) -> StrategyVote:
    close = _num(row, "close")
    atr_value = _num(row, "atr")
    if close <= 0 or atr_value <= 0 or idx < 20 or "atr" not in frame.columns:
        return StrategyVote("volatility_breakout", "flat", 0.0, 0.15, "insufficient_data")

    recent_atr = pd.to_numeric(frame.iloc[max(0, idx - 20):idx + 1]["atr"], errors="coerce").dropna()
    atr_mean = float(recent_atr.mean()) if len(recent_atr) else 0.0
    atr_ratio = atr_value / atr_mean if atr_mean > 0 else 1.0
    if atr_ratio < 1.25:
        return StrategyVote("volatility_breakout", "flat", 0.0, 0.15, "normal_volatility")

    breakout_high = _num(row, "breakout_high")
    breakout_low = _num(row, "breakout_low")
    confidence = _bounded_confidence((atr_ratio - 1.0) / 1.0)
    if breakout_high > 0 and close >= breakout_high:
        return StrategyVote("volatility_breakout", "long", confidence, 0.15, "range_expansion_up")
    if breakout_low > 0 and close <= breakout_low:
        return StrategyVote("volatility_breakout", "short", confidence, 0.15, "range_expansion_down")
    return StrategyVote("volatility_breakout", "flat", 0.0, 0.15, "no_breakout")


def ensemble_vote(
    row: pd.Series,
    params: StrategyParams,
    frame: pd.DataFrame,
    idx: int,
    base_score: float = 5.0,
) -> EnsembleResult:
    votes = [
        _vote_trend_breakout(row, params, frame, idx),
        _vote_mean_reversion(row),
        _vote_momentum(row, frame, idx),
        _vote_volatility_breakout(row, frame, idx),
    ]

    long_weight = sum(v.weight * v.confidence for v in votes if v.vote == "long")
    short_weight = sum(v.weight * v.confidence for v in votes if v.vote == "short")
    total_weight = sum(v.weight for v in votes) or 1.0

    if long_weight > short_weight and long_weight >= 0.10:
        final_side: Side = "long"
        support_weight = sum(v.weight for v in votes if v.vote == "long")
    elif short_weight > long_weight and short_weight >= 0.10:
        final_side = "short"
        support_weight = sum(v.weight for v in votes if v.vote == "short")
    else:
        final_side = "flat"
        support_weight = 0.0

    approval_rate = support_weight / total_weight
    score_bonus = 0.0
    if final_side == "flat":
        score_bonus = -2.0
    elif approval_rate >= 0.70:
        score_bonus = 1.5
    elif approval_rate >= 0.50:
        score_bonus = 0.5
    elif approval_rate < 0.30:
        score_bonus = -1.0

    final_score = max(1.0, min(10.0, float(base_score) + score_bonus))
    details = " | ".join(f"{v.strategy_name}:{v.vote}({v.confidence:.0%})" for v in votes)
    return EnsembleResult(final_side, final_score, votes, approval_rate, details)
