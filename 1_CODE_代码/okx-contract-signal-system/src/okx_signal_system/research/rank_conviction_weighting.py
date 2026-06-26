from __future__ import annotations

from collections.abc import Mapping, Sequence

import numpy as np


def _validate_side_weights(side_weights: Sequence[float], side_count: int) -> np.ndarray:
    weights = np.asarray(side_weights, dtype=float)
    if weights.ndim != 1 or len(weights) != side_count:
        raise ValueError("one side weight is required for every selected member")
    if np.any(~np.isfinite(weights)) or np.any(weights <= 0.0):
        raise ValueError("side weights must be strictly positive and finite")
    if not np.isclose(weights.sum(), 0.5, atol=1e-12):
        raise ValueError("side weights must sum to 0.5")
    if np.any(np.diff(weights) > 1e-12):
        raise ValueError("side weights must be non-increasing by conviction rank")
    return weights


def rank_conviction_weight_path(
    membership_weights: np.ndarray,
    score_rows: Sequence[Mapping[str, float]],
    symbols: Sequence[str],
    *,
    side_weights: Sequence[float],
    refresh_only_on_membership_change: bool = True,
) -> np.ndarray:
    """Reweight fixed long/short memberships by within-side momentum rank.

    The strongest selected long receives the first positive side weight and the
    weakest selected short receives the first absolute short weight. Direction
    and membership are inherited from the parent strategy. When requested, the
    target weights are refreshed only when either side's membership changes.
    """

    memberships = np.asarray(membership_weights, dtype=float)
    ordered_symbols = list(symbols)
    if memberships.ndim != 2 or memberships.shape[1] != len(ordered_symbols):
        raise ValueError("membership matrix must align with symbols")
    if memberships.shape[0] != len(score_rows):
        raise ValueError("one score row is required per membership row")

    positive_count = np.count_nonzero(memberships[0] > 0.0)
    negative_count = np.count_nonzero(memberships[0] < 0.0)
    if positive_count == 0 or positive_count != negative_count:
        raise ValueError("equal non-empty long and short sides are required")
    weights_by_rank = _validate_side_weights(side_weights, positive_count)

    output = np.zeros_like(memberships)
    previous_membership: np.ndarray | None = None
    previous_target: np.ndarray | None = None

    for row, mapping in enumerate(score_rows):
        membership = np.sign(memberships[row])
        if np.count_nonzero(membership > 0.0) != positive_count:
            raise ValueError("long membership count changed")
        if np.count_nonzero(membership < 0.0) != negative_count:
            raise ValueError("short membership count changed")

        if (
            refresh_only_on_membership_change
            and previous_membership is not None
            and previous_target is not None
            and np.array_equal(membership, previous_membership)
        ):
            output[row] = previous_target
            continue

        scores = np.asarray([float(mapping[symbol]) for symbol in ordered_symbols], dtype=float)
        if np.any(~np.isfinite(scores)):
            raise ValueError("finite scores are required")

        long_indices = np.flatnonzero(membership > 0.0)
        short_indices = np.flatnonzero(membership < 0.0)
        long_order = long_indices[np.argsort(-scores[long_indices], kind="stable")]
        short_order = short_indices[np.argsort(scores[short_indices], kind="stable")]

        target = np.zeros(memberships.shape[1], dtype=float)
        target[long_order] = weights_by_rank
        target[short_order] = -weights_by_rank
        output[row] = target
        previous_membership = membership.copy()
        previous_target = target.copy()

    return output
