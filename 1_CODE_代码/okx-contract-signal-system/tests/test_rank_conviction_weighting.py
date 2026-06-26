from __future__ import annotations

import numpy as np
import pytest

from okx_signal_system.research.rank_conviction_weighting import rank_conviction_weight_path

SYMBOLS = ["A", "B", "C", "D", "E", "F", "G", "H"]


def test_rank_conviction_weights_preserve_membership_and_direction() -> None:
    memberships = np.asarray(
        [
            [0.125, 0.125, 0.125, 0.125, -0.125, -0.125, -0.125, -0.125],
            [0.125, 0.125, 0.125, 0.125, -0.125, -0.125, -0.125, -0.125],
        ],
        dtype=float,
    )
    scores = [
        {"A": 8.0, "B": 7.0, "C": 6.0, "D": 5.0, "E": 4.0, "F": 3.0, "G": 2.0, "H": 1.0},
        {"A": 5.0, "B": 8.0, "C": 7.0, "D": 6.0, "E": 1.0, "F": 2.0, "G": 3.0, "H": 4.0},
    ]
    result = rank_conviction_weight_path(
        memberships,
        scores,
        SYMBOLS,
        side_weights=[0.20, 0.15, 0.10, 0.05],
        refresh_only_on_membership_change=True,
    )

    assert np.array_equal(np.sign(result), np.sign(memberships))
    assert np.allclose(result.sum(axis=1), 0.0)
    assert np.allclose(np.abs(result).sum(axis=1), 1.0)
    assert np.allclose(result[0], result[1])
    assert np.isclose(result[0, 0], 0.20)
    assert np.isclose(result[0, 7], -0.20)


def test_rank_conviction_refreshes_when_membership_changes() -> None:
    memberships = np.asarray(
        [
            [0.125, 0.125, 0.125, 0.125, -0.125, -0.125, -0.125, -0.125],
            [0.125, 0.125, 0.125, -0.125, 0.125, -0.125, -0.125, -0.125],
        ],
        dtype=float,
    )
    scores = [
        {symbol: float(8 - index) for index, symbol in enumerate(SYMBOLS)},
        {"A": 4.0, "B": 8.0, "C": 6.0, "D": 1.0, "E": 7.0, "F": 2.0, "G": 3.0, "H": 5.0},
    ]
    result = rank_conviction_weight_path(
        memberships,
        scores,
        SYMBOLS,
        side_weights=[0.20, 0.15, 0.10, 0.05],
    )

    assert not np.allclose(result[0], result[1])
    assert np.isclose(result[1, 1], 0.20)
    assert np.isclose(result[1, 3], -0.20)


def test_invalid_side_weights_fail_closed() -> None:
    memberships = np.asarray([[0.25, 0.25, -0.25, -0.25]], dtype=float)
    scores = [{"A": 4.0, "B": 3.0, "C": 2.0, "D": 1.0}]
    with pytest.raises(ValueError, match="sum to 0.5"):
        rank_conviction_weight_path(
            memberships,
            scores,
            ["A", "B", "C", "D"],
            side_weights=[0.20, 0.20],
        )
