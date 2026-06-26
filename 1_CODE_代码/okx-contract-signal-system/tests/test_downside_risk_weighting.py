from __future__ import annotations

import numpy as np
import pytest

from okx_signal_system.research.downside_risk_weighting import (
    downside_risk_weight_path,
    expected_shortfall_loss,
)


def test_expected_shortfall_loss_uses_left_tail() -> None:
    values = [-0.10, -0.04, -0.02, 0.01, 0.03, 0.05]
    assert np.isclose(expected_shortfall_loss(values, tail_fraction=1 / 3), 0.07)


def test_downside_risk_weighting_preserves_membership_and_side_gross() -> None:
    memberships = np.asarray(
        [
            [0.125, 0.125, -0.125, -0.125],
            [0.125, 0.125, -0.125, -0.125],
        ],
        dtype=float,
    )
    risks = np.asarray(
        [
            [0.01, 0.02, 0.03, 0.04],
            [0.04, 0.03, 0.02, 0.01],
        ],
        dtype=float,
    )
    result = downside_risk_weight_path(
        memberships,
        risks,
        side_gross=0.5,
        maximum_absolute_weight=0.4,
        refresh_only_on_membership_change=True,
    )
    assert np.array_equal(np.sign(result), np.sign(memberships))
    assert np.allclose(result.sum(axis=1), 0.0)
    assert np.allclose(np.abs(result).sum(axis=1), 1.0)
    assert np.allclose(result[0], result[1])
    assert result[0, 0] > result[0, 1]
    assert abs(result[0, 2]) > abs(result[0, 3])


def test_weight_cap_is_enforced_with_water_filling() -> None:
    memberships = np.asarray([[0.125, 0.125, 0.125, 0.125, -0.125, -0.125, -0.125, -0.125]])
    risks = np.asarray([[0.001, 0.01, 0.02, 0.03, 0.001, 0.01, 0.02, 0.03]])
    result = downside_risk_weight_path(
        memberships,
        risks,
        maximum_absolute_weight=0.20,
    )
    assert float(np.abs(result).max()) <= 0.20 + 1e-12
    assert np.isclose(result[result > 0.0].sum(), 0.5)
    assert np.isclose(-result[result < 0.0].sum(), 0.5)


def test_impossible_weight_cap_fails_closed() -> None:
    memberships = np.asarray([[0.25, 0.25, -0.25, -0.25]])
    risks = np.ones_like(memberships)
    with pytest.raises(ValueError, match="cannot fill"):
        downside_risk_weight_path(
            memberships,
            risks,
            maximum_absolute_weight=0.20,
        )
