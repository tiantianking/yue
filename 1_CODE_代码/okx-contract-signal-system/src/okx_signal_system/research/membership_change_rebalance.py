from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np

RebalanceMode = Literal["daily_equal_reset", "membership_change_only"]


@dataclass(frozen=True)
class RebalanceSimulation:
    gross_returns: np.ndarray
    transaction_costs: np.ndarray
    funding_returns: np.ndarray
    net_returns: np.ndarray
    turnovers: np.ndarray
    gross_exposures: np.ndarray
    net_exposures: np.ndarray
    rebalance_flags: np.ndarray
    start_weights: np.ndarray
    end_weights: np.ndarray


def _as_matrix(value: np.ndarray, *, name: str) -> np.ndarray:
    matrix = np.asarray(value, dtype=float)
    if matrix.ndim != 2 or matrix.shape[0] == 0 or matrix.shape[1] == 0:
        raise ValueError(f"{name} must be a non-empty two-dimensional matrix")
    if not np.isfinite(matrix).all():
        raise ValueError(f"{name} must contain only finite values")
    return matrix


def signed_membership(weights: np.ndarray, *, tolerance: float = 1e-12) -> np.ndarray:
    matrix = _as_matrix(weights, name="weights")
    membership = np.zeros_like(matrix, dtype=np.int8)
    membership[matrix > tolerance] = 1
    membership[matrix < -tolerance] = -1
    return membership


def membership_path_agreement(targets: np.ndarray, actual_start_weights: np.ndarray) -> float:
    desired = signed_membership(targets)
    actual = signed_membership(actual_start_weights)
    if desired.shape != actual.shape:
        raise ValueError("target and actual membership shapes must match")
    return float(np.mean(np.all(desired == actual, axis=1)))


def simulate_rebalance_policy(
    targets: np.ndarray,
    asset_returns: np.ndarray,
    funding_rates: np.ndarray,
    *,
    one_way_cost: float,
    adverse_funding_multiplier: float = 1.0,
    mode: RebalanceMode,
) -> RebalanceSimulation:
    """Simulate equal-reset or membership-change-only execution for a frozen target path.

    ``targets`` requires one row per decision timestamp. The last row has no
    following holding interval, so the simulation uses ``targets[:-1]``. In
    membership-change-only mode, unchanged signed membership carries the
    drifted end weights without trading. Any signed membership change resets
    the complete active portfolio to the frozen equal target weights.
    """

    target_matrix = _as_matrix(targets, name="targets")
    returns = _as_matrix(asset_returns, name="asset_returns")
    funding = _as_matrix(funding_rates, name="funding_rates")
    periods = target_matrix.shape[0] - 1
    if periods <= 0:
        raise ValueError("targets must contain at least two decision rows")
    expected_shape = (periods, target_matrix.shape[1])
    if returns.shape != expected_shape:
        raise ValueError(f"asset_returns must have shape {expected_shape}")
    if funding.shape != expected_shape:
        raise ValueError(f"funding_rates must have shape {expected_shape}")
    if one_way_cost < 0.0:
        raise ValueError("one_way_cost must be non-negative")
    if adverse_funding_multiplier < 1.0:
        raise ValueError("adverse_funding_multiplier must be at least one")
    if mode not in {"daily_equal_reset", "membership_change_only"}:
        raise ValueError(f"unsupported rebalance mode: {mode}")

    target_membership = signed_membership(target_matrix)
    start_weights = np.zeros(expected_shape, dtype=float)
    end_weights = np.zeros(expected_shape, dtype=float)
    gross_returns = np.zeros(periods, dtype=float)
    transaction_costs = np.zeros(periods, dtype=float)
    funding_returns = np.zeros(periods, dtype=float)
    net_returns = np.zeros(periods, dtype=float)
    turnovers = np.zeros(periods, dtype=float)
    gross_exposures = np.zeros(periods, dtype=float)
    net_exposures = np.zeros(periods, dtype=float)
    rebalance_flags = np.zeros(periods, dtype=bool)

    previous_end = np.zeros(target_matrix.shape[1], dtype=float)
    for index in range(periods):
        desired = target_matrix[index]
        membership_changed = index == 0 or not np.array_equal(
            target_membership[index], target_membership[index - 1]
        )
        rebalance = mode == "daily_equal_reset" or membership_changed
        if rebalance:
            start = desired.copy()
            turnover = float(np.abs(start - previous_end).sum())
        else:
            start = previous_end.copy()
            turnover = 0.0

        gross = float(np.dot(start, returns[index]))
        funding_components = -start * funding[index]
        stressed_funding = np.where(
            funding_components < 0.0,
            funding_components * adverse_funding_multiplier,
            funding_components,
        )
        funding_return = float(stressed_funding.sum())
        transaction_cost = turnover * one_way_cost
        net = gross - transaction_cost + funding_return

        equity_factor = 1.0 + gross
        if equity_factor <= 0.0:
            raise ValueError(f"non-positive gross equity factor at interval {index}")
        end = start * (1.0 + returns[index]) / equity_factor
        if not np.isfinite(end).all():
            raise ValueError(f"non-finite drifted weights at interval {index}")

        start_weights[index] = start
        end_weights[index] = end
        gross_returns[index] = gross
        transaction_costs[index] = transaction_cost
        funding_returns[index] = funding_return
        net_returns[index] = net
        turnovers[index] = turnover
        gross_exposures[index] = float(np.abs(start).sum())
        net_exposures[index] = float(start.sum())
        rebalance_flags[index] = rebalance
        previous_end = end

    terminal_turnover = float(np.abs(previous_end).sum())
    terminal_cost = terminal_turnover * one_way_cost
    turnovers[-1] += terminal_turnover
    transaction_costs[-1] += terminal_cost
    net_returns[-1] -= terminal_cost

    return RebalanceSimulation(
        gross_returns=gross_returns,
        transaction_costs=transaction_costs,
        funding_returns=funding_returns,
        net_returns=net_returns,
        turnovers=turnovers,
        gross_exposures=gross_exposures,
        net_exposures=net_exposures,
        rebalance_flags=rebalance_flags,
        start_weights=start_weights,
        end_weights=end_weights,
    )


def exposure_summary(simulation: RebalanceSimulation) -> dict[str, float]:
    absolute_net = np.abs(simulation.net_exposures)
    gross = simulation.gross_exposures
    return {
        "mean_absolute_net_exposure": float(absolute_net.mean()),
        "p95_absolute_net_exposure": float(np.quantile(absolute_net, 0.95)),
        "maximum_absolute_net_exposure": float(absolute_net.max()),
        "p05_gross_exposure": float(np.quantile(gross, 0.05)),
        "p95_gross_exposure": float(np.quantile(gross, 0.95)),
        "minimum_gross_exposure": float(gross.min()),
        "maximum_gross_exposure": float(gross.max()),
        "no_trade_decision_fraction": float(np.mean(~simulation.rebalance_flags[1:]))
        if len(simulation.rebalance_flags) > 1
        else 0.0,
    }
