from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import pandas as pd


def causal_recent_funding_mean(
    funding_time: pd.Series,
    funding_rate: pd.Series,
    entry_time: pd.Timestamp,
    *,
    settlements: int = 3,
) -> float | None:
    times = pd.to_datetime(funding_time, utc=True)
    rates = pd.to_numeric(funding_rate, errors="coerce")
    mask = times.le(pd.Timestamp(entry_time)) & rates.notna()
    values = rates.loc[mask].tail(settlements)
    if len(values) < settlements:
        return None
    return float(values.mean())


def carry_benefit(position_sign: float, funding_mean: float) -> float:
    if position_sign == 0.0:
        raise ValueError("non-zero position sign required")
    if not np.isfinite(funding_mean):
        raise ValueError("finite funding mean required")
    return float(-np.sign(position_sign) * funding_mean)


def ambiguous_side(scores: Sequence[float], *, tolerance: float = 1e-12) -> bool:
    ordered = np.sort(np.asarray(scores, dtype=float))
    if len(ordered) < 2:
        return False
    return bool(np.any(np.diff(ordered) <= tolerance))


def target_turnover(weights: np.ndarray) -> float:
    matrix = np.asarray(weights, dtype=float)
    if matrix.ndim != 2 or matrix.shape[0] == 0:
        raise ValueError("non-empty two-dimensional weights required")
    initial = float(np.abs(matrix[0]).sum())
    changes = float(np.abs(np.diff(matrix, axis=0)).sum()) if len(matrix) > 1 else 0.0
    final = float(np.abs(matrix[-1]).sum())
    return initial + changes + final
