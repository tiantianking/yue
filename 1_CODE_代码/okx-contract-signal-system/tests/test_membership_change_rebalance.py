from __future__ import annotations

import numpy as np
import pytest

from okx_signal_system.research.membership_change_rebalance import (
    exposure_summary,
    membership_path_agreement,
    simulate_rebalance_policy,
)


def _targets() -> np.ndarray:
    return np.asarray(
        [
            [0.25, 0.25, -0.25, -0.25],
            [0.25, 0.25, -0.25, -0.25],
            [0.25, 0.25, -0.25, -0.25],
            [0.25, 0.00, -0.25, 0.00],
        ],
        dtype=float,
    )


def test_membership_change_only_skips_equal_weight_reset_when_membership_is_unchanged() -> None:
    targets = _targets()
    returns = np.asarray(
        [
            [0.10, 0.00, 0.00, 0.00],
            [0.00, 0.00, 0.00, 0.00],
            [0.00, 0.00, 0.00, 0.00],
        ],
        dtype=float,
    )
    funding = np.zeros_like(returns)

    daily = simulate_rebalance_policy(
        targets,
        returns,
        funding,
        one_way_cost=0.001,
        mode="daily_equal_reset",
    )
    event = simulate_rebalance_policy(
        targets,
        returns,
        funding,
        one_way_cost=0.001,
        mode="membership_change_only",
    )

    assert daily.rebalance_flags.tolist() == [True, True, True]
    assert event.rebalance_flags.tolist() == [True, False, False]
    assert event.turnovers.sum() < daily.turnovers.sum()
    assert event.transaction_costs.sum() < daily.transaction_costs.sum()
    assert event.start_weights[1, 0] > 0.25


def test_signed_membership_change_triggers_full_equal_reset() -> None:
    targets = np.asarray(
        [
            [0.25, 0.25, -0.25, -0.25],
            [0.25, 0.25, -0.25, -0.25],
            [0.25, 0.00, -0.25, -0.25],
        ],
        dtype=float,
    )
    returns = np.asarray(
        [
            [0.08, 0.00, 0.00, 0.00],
            [0.00, 0.00, 0.00, 0.00],
        ],
        dtype=float,
    )
    funding = np.zeros_like(returns)
    result = simulate_rebalance_policy(
        targets,
        returns,
        funding,
        one_way_cost=0.0,
        mode="membership_change_only",
    )

    assert result.rebalance_flags.tolist() == [True, False]
    assert result.start_weights[1, 0] > 0.25

    changed_targets = targets.copy()
    changed_targets[1] = [0.25, 0.00, -0.25, -0.25]
    changed = simulate_rebalance_policy(
        changed_targets,
        returns,
        funding,
        one_way_cost=0.0,
        mode="membership_change_only",
    )
    assert changed.rebalance_flags.tolist() == [True, True]
    assert np.array_equal(changed.start_weights[1], changed_targets[1])


def test_terminal_liquidation_uses_actual_drifted_holdings() -> None:
    targets = np.asarray(
        [
            [0.5, -0.5],
            [0.5, -0.5],
        ],
        dtype=float,
    )
    returns = np.asarray([[0.10, 0.00]], dtype=float)
    funding = np.zeros_like(returns)
    result = simulate_rebalance_policy(
        targets,
        returns,
        funding,
        one_way_cost=0.01,
        mode="membership_change_only",
    )

    expected_terminal = float(np.abs(result.end_weights[-1]).sum())
    assert result.turnovers[-1] == pytest.approx(1.0 + expected_terminal)
    assert result.transaction_costs[-1] == pytest.approx((1.0 + expected_terminal) * 0.01)


def test_membership_agreement_ignores_weight_drift_but_not_side_changes() -> None:
    targets = np.asarray(
        [
            [0.25, 0.25, -0.25, -0.25],
            [0.25, 0.25, -0.25, -0.25],
        ],
        dtype=float,
    )
    actual = np.asarray(
        [
            [0.20, 0.30, -0.22, -0.28],
            [0.35, 0.15, -0.10, -0.40],
        ],
        dtype=float,
    )
    assert membership_path_agreement(targets, actual) == pytest.approx(1.0)
    actual[1, 1] = -0.15
    assert membership_path_agreement(targets, actual) == pytest.approx(0.5)


def test_exposure_summary_reports_drift_and_no_trade_fraction() -> None:
    targets = _targets()
    returns = np.asarray(
        [
            [0.10, 0.00, 0.00, 0.00],
            [0.00, 0.00, 0.00, 0.00],
            [0.00, 0.00, 0.00, 0.00],
        ],
        dtype=float,
    )
    result = simulate_rebalance_policy(
        targets,
        returns,
        np.zeros_like(returns),
        one_way_cost=0.0,
        mode="membership_change_only",
    )
    summary = exposure_summary(result)
    assert summary["mean_absolute_net_exposure"] > 0.0
    assert summary["no_trade_decision_fraction"] == pytest.approx(1.0)


def test_invalid_shapes_and_costs_are_rejected() -> None:
    targets = np.zeros((2, 2), dtype=float)
    returns = np.zeros((1, 2), dtype=float)
    funding = np.zeros((1, 2), dtype=float)
    with pytest.raises(ValueError, match="non-negative"):
        simulate_rebalance_policy(
            targets,
            returns,
            funding,
            one_way_cost=-0.001,
            mode="membership_change_only",
        )
    with pytest.raises(ValueError, match="shape"):
        simulate_rebalance_policy(
            targets,
            np.zeros((2, 2), dtype=float),
            funding,
            one_way_cost=0.0,
            mode="membership_change_only",
        )
