from __future__ import annotations

from dataclasses import dataclass, replace

from okx_signal_system.signal_quality.candidate import SignalCandidate
from okx_signal_system.signal_quality.ranker import rank_candidates


@dataclass(frozen=True)
class TieredSelection:
    ranked: list[SignalCandidate]
    tier_a: list[SignalCandidate]
    tier_b: list[SignalCandidate]
    tier_c: list[SignalCandidate]


def assign_tiers(
    candidates: list[SignalCandidate],
    *,
    max_tier_a: int = 2,
) -> TieredSelection:
    ranked = rank_candidates(candidates)
    tiered: list[SignalCandidate] = []
    for idx, candidate in enumerate(ranked):
        tier = "A" if idx < max_tier_a else "B"
        tiered.append(replace(candidate, tier=tier))
    return TieredSelection(
        ranked=tiered,
        tier_a=[item for item in tiered if item.tier == "A"],
        tier_b=[item for item in tiered if item.tier == "B"],
        tier_c=[],
    )
