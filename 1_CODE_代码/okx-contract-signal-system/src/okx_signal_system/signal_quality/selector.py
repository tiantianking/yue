from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace

import pandas as pd

from okx_signal_system.signal_quality.candidate import SignalCandidate
from okx_signal_system.signal_quality.correlation import assign_correlation_groups
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
    price_history: Mapping[str, pd.DataFrame] | None = None,
    high_correlation_threshold: float = 0.75,
    correlation_window_days: int = 30,
    min_correlation_samples: int = 8,
) -> TieredSelection:
    ranked = rank_candidates(candidates)
    group_by_symbol = assign_correlation_groups(
        ranked,
        price_history,
        threshold=high_correlation_threshold,
        window_days=correlation_window_days,
        min_samples=min_correlation_samples,
    )
    used_groups: set[str] = set()
    tier_a_count = 0
    tiered: list[SignalCandidate] = []
    for candidate in ranked:
        key = f"{candidate.side}:{candidate.inst_id}"
        group = group_by_symbol.get(key, f"solo:{key}")
        is_formal_trigger = bool(candidate.health_item.get("would_push"))
        tier = "B" if is_formal_trigger else "C"
        if is_formal_trigger and tier_a_count < max_tier_a and group not in used_groups:
            tier = "A"
            tier_a_count += 1
            used_groups.add(group)
        tiered.append(replace(candidate, tier=tier, correlation_group=group))
    return TieredSelection(
        ranked=tiered,
        tier_a=[item for item in tiered if item.tier == "A"],
        tier_b=[item for item in tiered if item.tier == "B"],
        tier_c=[item for item in tiered if item.tier == "C"],
    )
