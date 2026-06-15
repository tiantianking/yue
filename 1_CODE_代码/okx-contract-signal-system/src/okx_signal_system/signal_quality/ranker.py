from __future__ import annotations

from dataclasses import replace

from okx_signal_system.signal_quality.candidate import SignalCandidate


def rank_candidates(candidates: list[SignalCandidate]) -> list[SignalCandidate]:
    ranked = sorted(
        candidates,
        key=lambda item: (
            -float(item.rank_score),
            -float(item.raw_score),
            item.inst_id,
        ),
    )
    return [replace(item, rank=idx + 1) for idx, item in enumerate(ranked)]
