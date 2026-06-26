from __future__ import annotations

from collections.abc import Sequence

import numpy as np


def expected_shortfall_loss(values: Sequence[float], *, tail_fraction: float = 0.05) -> float:
    clean = np.asarray(values, dtype=float)
    clean = clean[np.isfinite(clean)]
    if clean.size == 0:
        raise ValueError("finite observations required")
    if not 0.0 < tail_fraction <= 0.5:
        raise ValueError("tail_fraction must be in (0, 0.5]")
    count = max(1, int(np.ceil(clean.size * tail_fraction)))
    tail_mean = float(np.partition(clean, count - 1)[:count].mean())
    return max(-tail_mean, 1e-8)


def _capped_inverse_risk_allocation(
    risks: np.ndarray,
    *,
    side_gross: float,
    maximum_absolute_weight: float,
) -> np.ndarray:
    if risks.ndim != 1 or risks.size == 0:
        raise ValueError("one-dimensional non-empty risk vector required")
    if np.any(~np.isfinite(risks)) or np.any(risks <= 0.0):
        raise ValueError("strictly positive finite risks required")
    if side_gross <= 0.0:
        raise ValueError("side_gross must be positive")
    if maximum_absolute_weight <= 0.0:
        raise ValueError("maximum_absolute_weight must be positive")
    if maximum_absolute_weight * risks.size + 1e-12 < side_gross:
        raise ValueError("weight cap cannot fill requested side gross")

    inverse = 1.0 / risks
    weights = np.zeros_like(inverse)
    active = np.ones(inverse.size, dtype=bool)
    remaining = float(side_gross)
    while np.any(active):
        active_inverse = inverse[active]
        proposal = remaining * active_inverse / active_inverse.sum()
        active_indices = np.flatnonzero(active)
        capped = proposal > maximum_absolute_weight + 1e-15
        if not np.any(capped):
            weights[active_indices] = proposal
            remaining = 0.0
            break
        capped_indices = active_indices[capped]
        weights[capped_indices] = maximum_absolute_weight
        remaining -= maximum_absolute_weight * len(capped_indices)
        active[capped_indices] = False
        if remaining < -1e-12:
            raise ValueError("invalid capped allocation")
    if not np.isclose(weights.sum(), side_gross, atol=1e-10):
        raise ValueError("allocation does not sum to side gross")
    return weights


def downside_risk_weight_path(
    membership_weights: np.ndarray,
    risk_matrix: np.ndarray,
    *,
    side_gross: float = 0.5,
    maximum_absolute_weight: float = 0.20,
    refresh_only_on_membership_change: bool = True,
) -> np.ndarray:
    """Risk-balance fixed long/short memberships without changing their direction."""

    memberships = np.asarray(membership_weights, dtype=float)
    risks = np.asarray(risk_matrix, dtype=float)
    if memberships.ndim != 2 or risks.shape != memberships.shape:
        raise ValueError("membership and risk matrices must have identical two-dimensional shapes")
    output = np.zeros_like(memberships)
    previous_membership: np.ndarray | None = None
    previous_target: np.ndarray | None = None

    for row in range(memberships.shape[0]):
        current_membership = np.sign(memberships[row])
        if (
            refresh_only_on_membership_change
            and previous_membership is not None
            and previous_target is not None
            and np.array_equal(current_membership, previous_membership)
        ):
            output[row] = previous_target
            continue

        long_indices = np.flatnonzero(current_membership > 0.0)
        short_indices = np.flatnonzero(current_membership < 0.0)
        if len(long_indices) == 0 or len(short_indices) == 0:
            raise ValueError("both long and short memberships are required")
        long_weights = _capped_inverse_risk_allocation(
            risks[row, long_indices],
            side_gross=side_gross,
            maximum_absolute_weight=maximum_absolute_weight,
        )
        short_weights = _capped_inverse_risk_allocation(
            risks[row, short_indices],
            side_gross=side_gross,
            maximum_absolute_weight=maximum_absolute_weight,
        )
        target = np.zeros(memberships.shape[1], dtype=float)
        target[long_indices] = long_weights
        target[short_indices] = -short_weights
        output[row] = target
        previous_membership = current_membership.copy()
        previous_target = target.copy()

    return output
