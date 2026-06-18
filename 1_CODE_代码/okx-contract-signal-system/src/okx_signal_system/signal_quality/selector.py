from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

import pandas as pd

from okx_signal_system.signal_quality.candidate import CandidateLike, ObservationCandidate, SignalCandidate
from okx_signal_system.signal_quality.correlation import (
    DEFAULT_MIN_CORRELATION_SAMPLES,
    assign_correlation_groups,
)


DEFAULT_MAX_A_PER_CYCLE = 4
DEFAULT_MAX_A_PER_CORRELATION_GROUP = 1
DEFAULT_MIN_A_QUALITY_SCORE = 80.0
DEFAULT_MIN_B_QUALITY_SCORE = 68.0


@dataclass(frozen=True)
class TieredSelection:
    ranked: list[CandidateLike]
    tier_a: list[SignalCandidate]
    tier_b: list[SignalCandidate]
    tier_c: list[CandidateLike]


def _float_or_none(value: Any) -> float | None:
    try:
        if value is None:
            return None
        number = float(value)
    except (TypeError, ValueError):
        return None
    if pd.isna(number):
        return None
    return number


def _score_0_to_10(candidate: SignalCandidate) -> float:
    for value in (
        candidate.health_item.get("final_score"),
        candidate.health_item.get("raw_score"),
        getattr(candidate.decision, "signal_score", None),
        getattr(candidate.signal, "signal_score", None),
        candidate.raw_score,
    ):
        number = _float_or_none(value)
        if number is not None:
            return max(1.0, min(10.0, number))
    return 5.0


def _rr_value(candidate: SignalCandidate) -> float:
    for value in (
        getattr(candidate.decision, "risk_reward_ratio", None),
        getattr(candidate.signal, "risk_reward_ratio", None),
    ):
        number = _float_or_none(value)
        if number is not None:
            return max(0.0, number)
    return 0.0


def _trend_component(candidate: SignalCandidate) -> float:
    bias = str(candidate.health_item.get("bias") or "").lower()
    side = candidate.side.lower()
    if side and side in bias:
        return 15.0
    if bias in {"flat", "range", "sideways"}:
        return 6.0
    return 12.0


def _structure_component(candidate: SignalCandidate) -> float:
    return _score_0_to_10(candidate) * 6.0


def _volume_component(candidate: SignalCandidate) -> float:
    quality_model = candidate.health_item.get("quality_model")
    if isinstance(quality_model, dict):
        model_score = _float_or_none(quality_model.get("score") or quality_model.get("quality_score"))
        if model_score is not None:
            if model_score <= 1.0:
                return max(0.0, min(10.0, model_score * 10.0))
            return max(0.0, min(10.0, model_score / 100.0 * 10.0))
    return 8.0


def _market_component(candidate: SignalCandidate) -> float:
    regime = str(candidate.health_item.get("regime") or "").lower()
    if regime in {"high_vol_trend", "low_vol_trend"}:
        return 5.0
    if regime in {"high_vol_range", "low_vol_range"}:
        return 2.0
    return 4.0


def _rr_component(candidate: SignalCandidate) -> float:
    rr = _rr_value(candidate)
    if rr <= 0:
        return 0.0
    score = min(7.0, rr / 3.5 * 7.0)
    stop_pct = _float_or_none(getattr(candidate.decision, "stop_distance_pct", None))
    if stop_pct is not None and stop_pct > 0.02:
        score -= min(3.0, (stop_pct - 0.02) * 150.0)
    return max(0.0, score)


def _freshness_component(candidate: SignalCandidate) -> float:
    reason = str(candidate.health_item.get("reason") or "").lower()
    if bool(candidate.health_item.get("would_push")) and reason in {"", "ready"}:
        return 3.0
    return 2.0


def quality_score_breakdown(candidate: SignalCandidate) -> dict[str, float]:
    breakdown = {
        "trend_consistency": _trend_component(candidate),
        "structure_quality": _structure_component(candidate),
        "volume_liquidity": _volume_component(candidate),
        "market_regime_fit": _market_component(candidate),
        "cost_adjusted_rr": _rr_component(candidate),
        "freshness_data_quality": _freshness_component(candidate),
        "correlation_penalty": 0.0,
    }
    breakdown["total"] = max(0.0, min(100.0, sum(breakdown.values())))
    return breakdown


def absolute_quality_score(candidate: SignalCandidate) -> float:
    return float(quality_score_breakdown(candidate)["total"])


def _absolute_tier(score: float, *, min_a_quality_score: float, min_b_quality_score: float) -> str:
    if score >= min_a_quality_score:
        return "A"
    if score >= min_b_quality_score:
        return "B"
    return "C"


def _annotate_quality(candidate: SignalCandidate, *, score: float, breakdown: dict[str, float], absolute_tier: str) -> None:
    candidate.health_item["quality_score"] = float(score)
    candidate.health_item["quality_breakdown"] = dict(breakdown)
    candidate.health_item["absolute_tier"] = absolute_tier
    if isinstance(candidate.payload, dict):
        candidate.payload["quality_score"] = float(score)
        candidate.payload["quality_breakdown"] = dict(breakdown)
        candidate.payload["absolute_tier"] = absolute_tier


def _rank_formal_candidates(candidates: list[SignalCandidate]) -> list[SignalCandidate]:
    ranked = sorted(
        candidates,
        key=lambda item: (
            -float(item.health_item.get("quality_score", 0.0)),
            -float(item.rank_score),
            -float(item.raw_score),
            item.inst_id,
        ),
    )
    return [replace(item, rank=idx + 1) for idx, item in enumerate(ranked)]


def assign_tiers(
    candidates: list[SignalCandidate],
    *,
    observation_candidates: list[ObservationCandidate] | None = None,
    max_tier_a: int | None = None,
    max_a_per_cycle: int | None = None,
    max_a_per_correlation_group: int = DEFAULT_MAX_A_PER_CORRELATION_GROUP,
    min_a_quality_score: float = DEFAULT_MIN_A_QUALITY_SCORE,
    min_b_quality_score: float = DEFAULT_MIN_B_QUALITY_SCORE,
    price_history: Mapping[str, pd.DataFrame] | None = None,
    high_correlation_threshold: float = 0.75,
    correlation_window_days: int = 30,
    min_correlation_samples: int = DEFAULT_MIN_CORRELATION_SAMPLES,
) -> TieredSelection:
    observations = observation_candidates or []
    formal_candidates = [candidate for candidate in candidates if bool(candidate.health_item.get("would_push"))]
    for candidate in formal_candidates:
        breakdown = quality_score_breakdown(candidate)
        score = float(breakdown["total"])
        _annotate_quality(
            candidate,
            score=score,
            breakdown=breakdown,
            absolute_tier=_absolute_tier(
                score,
                min_a_quality_score=min_a_quality_score,
                min_b_quality_score=min_b_quality_score,
            ),
        )
    ranked_formal = _rank_formal_candidates(formal_candidates)
    ranked_observations = [
        replace(candidate, rank=None, watch_rank=idx + 1)
        for idx, candidate in enumerate(
            sorted(
                observations,
                key=lambda item: (
                    -float(item.rank_score),
                    -float(item.raw_score),
                    item.inst_id,
                ),
            )
        )
    ]
    if max_a_per_cycle is None:
        max_a_per_cycle = max_tier_a if max_tier_a is not None else DEFAULT_MAX_A_PER_CYCLE
    max_a_per_cycle = max(0, int(max_a_per_cycle))
    max_a_per_correlation_group = max(0, int(max_a_per_correlation_group))
    group_by_symbol = assign_correlation_groups(
        ranked_formal,
        price_history,
        threshold=high_correlation_threshold,
        window_days=correlation_window_days,
        min_samples=min_correlation_samples,
    )
    observation_group_by_symbol = assign_correlation_groups(
        ranked_observations,
        price_history,
        threshold=high_correlation_threshold,
        window_days=correlation_window_days,
        min_samples=min_correlation_samples,
    )
    used_groups: dict[str, int] = {}
    tier_a_count = 0
    tiered: list[CandidateLike] = []
    for candidate in ranked_formal:
        key = f"{candidate.side}:{candidate.inst_id}"
        group = group_by_symbol.get(key, f"solo:{key}")
        absolute_tier = str(candidate.health_item.get("absolute_tier") or "C")
        tier = absolute_tier
        tier_reason = "absolute_quality"
        if absolute_tier == "A":
            if tier_a_count >= max_a_per_cycle:
                tier = "B"
                tier_reason = "a_cycle_capacity"
            elif used_groups.get(group, 0) >= max_a_per_correlation_group:
                tier = "B"
                tier_reason = "a_correlation_capacity"
                breakdown = dict(candidate.health_item.get("quality_breakdown") or {})
                breakdown["correlation_penalty"] = -15.0
                candidate.health_item["quality_breakdown"] = breakdown
                if isinstance(candidate.payload, dict):
                    candidate.payload["quality_breakdown"] = breakdown
            else:
                tier = "A"
                tier_reason = "absolute_quality"
                tier_a_count += 1
                used_groups[group] = used_groups.get(group, 0) + 1
        else:
            tier = absolute_tier
            tier_reason = "below_a_quality_threshold" if tier == "B" else "below_b_quality_threshold"
        candidate.health_item["tier_reason"] = tier_reason
        candidate.health_item["a_capacity"] = max_a_per_cycle
        candidate.health_item["a_correlation_group_capacity"] = max_a_per_correlation_group
        candidate.health_item["quality_band"] = "A" if tier == "A" else "B"
        if isinstance(candidate.payload, dict):
            candidate.payload["tier_reason"] = tier_reason
            candidate.payload["quality_band"] = "A" if tier == "A" else "B"
        tiered.append(replace(candidate, tier=tier, correlation_group=group))

    for candidate in ranked_observations:
        key = f"{candidate.side}:{candidate.inst_id}"
        group = observation_group_by_symbol.get(key, f"solo:{key}")
        tiered.append(replace(candidate, tier="C", rank=None, correlation_group=group))
    return TieredSelection(
        ranked=tiered,
        tier_a=[item for item in tiered if item.tier == "A" and isinstance(item, SignalCandidate)],
        tier_b=[item for item in tiered if item.tier == "B" and isinstance(item, SignalCandidate)],
        tier_c=[item for item in tiered if item.tier == "C"],
    )


__all__ = [
    "DEFAULT_MAX_A_PER_CORRELATION_GROUP",
    "DEFAULT_MAX_A_PER_CYCLE",
    "DEFAULT_MIN_A_QUALITY_SCORE",
    "DEFAULT_MIN_B_QUALITY_SCORE",
    "TieredSelection",
    "absolute_quality_score",
    "assign_tiers",
    "quality_score_breakdown",
]
