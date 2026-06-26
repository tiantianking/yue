from __future__ import annotations

"""Fail-closed robustness screen for frozen research candidates.

The screen consumes candidate-generated evidence rather than reconstructing
hypothetical trades from incomplete output. This keeps the checks causal and
prevents a generic validator from inventing delayed prices or placebo samples.
"""

from dataclasses import dataclass
import math
from pathlib import Path
from typing import Any

import pandas as pd


FALSIFICATION_FILENAME = "falsification_trials.csv"
NEIGHBORHOOD_FILENAME = "parameter_neighborhood.csv"
PORTFOLIO_INCREMENT_FILENAME = "portfolio_increment.csv"


@dataclass(frozen=True)
class RobustnessThresholds:
    minimum_random_time_trials: int = 500
    random_time_alpha: float = 0.05
    entry_delay_bars: int = 1
    minimum_neighbor_variants: int = 3
    minimum_positive_neighbor_ratio: float = 2.0 / 3.0
    minimum_neighbor_median_pf: float = 1.0
    minimum_delay_pf: float = 1.0
    minimum_delay_net_retention: float = 0.35
    minimum_direction_pf_gap: float = 0.10
    maximum_primary_neighbor_pf_ratio: float = 2.0
    maximum_pf_deterioration: float = 0.03
    maximum_drawdown_deterioration: float = 0.02
    maximum_loss_streak_deterioration: int = 1
    minimum_pf_improvement: float = 0.03
    minimum_drawdown_improvement: float = 0.02
    minimum_loss_streak_improvement: int = 2
    minimum_signal_count_improvement_ratio: float = 0.10
    minimum_signal_count_improvement_absolute: int = 5
    minimum_regime_coverage_improvement: int = 1


DEFAULT_THRESHOLDS = RobustnessThresholds()


class RobustnessEvidenceError(ValueError):
    pass


def frozen_protocol_ok(protocol: Any) -> tuple[bool, str]:
    if not isinstance(protocol, dict):
        return False, "robustness_protocol object is required"
    expected = {
        "schema": "okx_robustness_screen_protocol_v1",
        "random_time_trials": DEFAULT_THRESHOLDS.minimum_random_time_trials,
        "random_time_alpha": DEFAULT_THRESHOLDS.random_time_alpha,
        "entry_delay_bars": DEFAULT_THRESHOLDS.entry_delay_bars,
        "minimum_neighbor_variants": DEFAULT_THRESHOLDS.minimum_neighbor_variants,
        "minimum_positive_neighbor_ratio": DEFAULT_THRESHOLDS.minimum_positive_neighbor_ratio,
        "portfolio_increment_required": True,
        "locked_before_pnl": True,
    }
    problems: list[str] = []
    for key, expected_value in expected.items():
        actual = protocol.get(key)
        if isinstance(expected_value, float):
            try:
                matches = math.isclose(float(actual), expected_value, rel_tol=0.0, abs_tol=1e-12)
            except (TypeError, ValueError):
                matches = False
        else:
            matches = actual == expected_value
        if not matches:
            problems.append(f"{key}={actual!r} expected={expected_value!r}")
    return not problems, "; ".join(problems) or "frozen robustness protocol accepted"


def _read_csv(path: Path, required_columns: set[str]) -> pd.DataFrame:
    if not path.is_file():
        raise RobustnessEvidenceError(f"missing evidence file: {path.name}")
    try:
        frame = pd.read_csv(path)
    except Exception as exc:
        raise RobustnessEvidenceError(f"invalid evidence file {path.name}: {exc}") from exc
    missing = sorted(required_columns - set(frame.columns))
    if missing:
        raise RobustnessEvidenceError(f"{path.name} missing columns: {', '.join(missing)}")
    if frame.empty:
        raise RobustnessEvidenceError(f"empty evidence file: {path.name}")
    return frame


def _finite_number(value: Any, *, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise RobustnessEvidenceError(f"{label} must be numeric") from exc
    if not math.isfinite(number):
        raise RobustnessEvidenceError(f"{label} must be finite")
    return number


def _single_test_row(frame: pd.DataFrame, test_name: str) -> pd.Series:
    rows = frame.loc[frame["test"].astype(str) == test_name]
    if len(rows) != 1:
        raise RobustnessEvidenceError(f"falsification test {test_name} requires exactly one row; got {len(rows)}")
    return rows.iloc[0]


def _check(name: str, ok: bool, detail: str) -> dict[str, Any]:
    return {"name": name, "ok": bool(ok), "detail": detail}


def evaluate_falsification_trials(
    frame: pd.DataFrame,
    *,
    thresholds: RobustnessThresholds = DEFAULT_THRESHOLDS,
) -> dict[str, Any]:
    observed = _single_test_row(frame, "observed")
    reversed_row = _single_test_row(frame, "direction_reversed")
    delayed = _single_test_row(frame, f"entry_delay_{thresholds.entry_delay_bars}bar")
    random_rows = frame.loc[frame["test"].astype(str) == "random_time"].copy()
    if len(random_rows) < thresholds.minimum_random_time_trials:
        raise RobustnessEvidenceError(
            f"random_time trials={len(random_rows)} minimum={thresholds.minimum_random_time_trials}"
        )

    for column in ("net_r", "profit_factor", "total_trades"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    if frame[["net_r", "profit_factor", "total_trades"]].isna().any().any():
        raise RobustnessEvidenceError("falsification trials contain non-numeric metrics")
    numeric_values = frame[["net_r", "profit_factor", "total_trades"]].to_numpy().ravel()
    if not all(math.isfinite(float(value)) for value in numeric_values):
        raise RobustnessEvidenceError("falsification trials contain non-finite metrics")

    observed_net = _finite_number(observed["net_r"], label="observed.net_r")
    observed_pf = _finite_number(observed["profit_factor"], label="observed.profit_factor")
    reverse_net = _finite_number(reversed_row["net_r"], label="direction_reversed.net_r")
    reverse_pf = _finite_number(reversed_row["profit_factor"], label="direction_reversed.profit_factor")
    delay_net = _finite_number(delayed["net_r"], label="entry_delay.net_r")
    delay_pf = _finite_number(delayed["profit_factor"], label="entry_delay.profit_factor")
    random_net = pd.to_numeric(random_rows["net_r"], errors="raise").astype(float)
    placebo_p = float((1 + int((random_net >= observed_net).sum())) / (len(random_net) + 1))
    placebo_q95 = float(random_net.quantile(0.95))
    delay_retention = float(delay_net / observed_net) if observed_net > 0.0 else float("-inf")
    direction_pf_gap = float(observed_pf - reverse_pf)
    direction_net_ratio = float(reverse_net / observed_net) if observed_net > 0.0 else float("inf")

    checks = [
        _check(
            "random_time_placebo",
            observed_net > placebo_q95 and placebo_p <= thresholds.random_time_alpha,
            f"observed_net_r={observed_net:.6f} q95={placebo_q95:.6f} empirical_p={placebo_p:.6f} trials={len(random_rows)}",
        ),
        _check(
            "direction_reversal_asymmetry",
            observed_net > 0.0
            and direction_pf_gap >= thresholds.minimum_direction_pf_gap
            and direction_net_ratio <= 0.25,
            f"observed_pf={observed_pf:.6f} reversed_pf={reverse_pf:.6f} pf_gap={direction_pf_gap:.6f} reversed_net_ratio={direction_net_ratio:.6f}",
        ),
        _check(
            "entry_delay_survival",
            delay_pf >= thresholds.minimum_delay_pf
            and delay_net > 0.0
            and delay_retention >= thresholds.minimum_delay_net_retention,
            f"delay_pf={delay_pf:.6f} delay_net_r={delay_net:.6f} retention={delay_retention:.6f}",
        ),
    ]
    return {
        "checks": checks,
        "metrics": {
            "random_time_trial_count": int(len(random_rows)),
            "random_time_empirical_p": placebo_p,
            "random_time_net_r_q95": placebo_q95,
            "direction_pf_gap": direction_pf_gap,
            "direction_reversed_net_ratio": direction_net_ratio,
            "entry_delay_net_retention": delay_retention,
        },
    }


def evaluate_parameter_neighborhood(
    frame: pd.DataFrame,
    *,
    thresholds: RobustnessThresholds = DEFAULT_THRESHOLDS,
) -> dict[str, Any]:
    frame = frame.copy()
    frame["is_primary"] = frame["is_primary"].astype(str).str.strip().str.lower().isin({"true", "1", "yes"})
    for column in ("distance", "net_r", "profit_factor", "total_trades"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    if frame[["distance", "net_r", "profit_factor", "total_trades"]].isna().any().any():
        raise RobustnessEvidenceError("parameter neighborhood contains non-numeric metrics")
    primary = frame.loc[frame["is_primary"]]
    if len(primary) != 1:
        raise RobustnessEvidenceError(f"parameter neighborhood requires exactly one primary row; got {len(primary)}")
    neighbors = frame.loc[(~frame["is_primary"]) & (frame["distance"] > 0.0) & (frame["distance"] <= 1.0)]
    if len(neighbors) < thresholds.minimum_neighbor_variants:
        raise RobustnessEvidenceError(
            f"neighbor variants={len(neighbors)} minimum={thresholds.minimum_neighbor_variants}"
        )

    primary_pf = _finite_number(primary.iloc[0]["profit_factor"], label="primary.profit_factor")
    neighbor_pf = neighbors["profit_factor"].astype(float)
    neighbor_net = neighbors["net_r"].astype(float)
    positive_ratio = float((neighbor_net > 0.0).mean())
    median_pf = float(neighbor_pf.median())
    spike_ratio = float(primary_pf / median_pf) if median_pf > 0.0 else float("inf")

    checks = [
        _check(
            "parameter_neighborhood_positive_ratio",
            positive_ratio >= thresholds.minimum_positive_neighbor_ratio,
            f"positive_ratio={positive_ratio:.6f} minimum={thresholds.minimum_positive_neighbor_ratio:.6f} neighbors={len(neighbors)}",
        ),
        _check(
            "parameter_neighborhood_median_pf",
            median_pf >= thresholds.minimum_neighbor_median_pf,
            f"median_pf={median_pf:.6f} minimum={thresholds.minimum_neighbor_median_pf:.6f}",
        ),
        _check(
            "parameter_is_not_isolated_spike",
            spike_ratio <= thresholds.maximum_primary_neighbor_pf_ratio,
            f"primary_pf={primary_pf:.6f} neighbor_median_pf={median_pf:.6f} ratio={spike_ratio:.6f}",
        ),
    ]
    return {
        "checks": checks,
        "metrics": {
            "neighbor_count": int(len(neighbors)),
            "positive_neighbor_ratio": positive_ratio,
            "neighbor_median_pf": median_pf,
            "primary_to_neighbor_median_pf_ratio": spike_ratio,
        },
    }


def evaluate_portfolio_increment(
    frame: pd.DataFrame,
    *,
    thresholds: RobustnessThresholds = DEFAULT_THRESHOLDS,
) -> dict[str, Any]:
    frame = frame.copy()
    baseline = frame.loc[frame["scenario"].astype(str) == "baseline"]
    combined = frame.loc[frame["scenario"].astype(str) == "combined"]
    if len(baseline) != 1 or len(combined) != 1:
        raise RobustnessEvidenceError(
            f"portfolio increment requires one baseline and one combined row; got baseline={len(baseline)} combined={len(combined)}"
        )
    metrics = (
        "profit_factor",
        "max_drawdown",
        "max_loss_streak",
        "effective_signal_count",
        "regime_coverage_count",
    )
    values: dict[str, tuple[float, float]] = {}
    for metric in metrics:
        values[metric] = (
            _finite_number(baseline.iloc[0][metric], label=f"baseline.{metric}"),
            _finite_number(combined.iloc[0][metric], label=f"combined.{metric}"),
        )

    base_pf, combined_pf = values["profit_factor"]
    base_dd, combined_dd = values["max_drawdown"]
    base_streak, combined_streak = values["max_loss_streak"]
    base_signals, combined_signals = values["effective_signal_count"]
    base_regimes, combined_regimes = values["regime_coverage_count"]

    no_material_deterioration = (
        combined_pf >= base_pf - thresholds.maximum_pf_deterioration
        and combined_dd <= base_dd + thresholds.maximum_drawdown_deterioration
        and combined_streak <= base_streak + thresholds.maximum_loss_streak_deterioration
    )
    signal_gain_required = max(
        thresholds.minimum_signal_count_improvement_absolute,
        int(math.ceil(base_signals * thresholds.minimum_signal_count_improvement_ratio)),
    )
    improvements = {
        "profit_factor": combined_pf >= base_pf + thresholds.minimum_pf_improvement,
        "max_drawdown": combined_dd <= base_dd - thresholds.minimum_drawdown_improvement,
        "max_loss_streak": combined_streak <= base_streak - thresholds.minimum_loss_streak_improvement,
        "effective_signal_count": combined_signals >= base_signals + signal_gain_required,
        "regime_coverage_count": combined_regimes >= base_regimes + thresholds.minimum_regime_coverage_improvement,
    }
    checks = [
        _check(
            "portfolio_no_material_deterioration",
            no_material_deterioration,
            f"pf={base_pf:.6f}->{combined_pf:.6f} dd={base_dd:.6f}->{combined_dd:.6f} loss_streak={base_streak:.0f}->{combined_streak:.0f}",
        ),
        _check(
            "portfolio_incremental_value",
            any(improvements.values()),
            ", ".join(f"{name}={passed}" for name, passed in improvements.items()),
        ),
    ]
    return {
        "checks": checks,
        "metrics": {
            "improvements": improvements,
            "required_signal_gain": signal_gain_required,
            "baseline": {metric: pair[0] for metric, pair in values.items()},
            "combined": {metric: pair[1] for metric, pair in values.items()},
        },
    }


def evaluate_robustness_screen(
    artifact_dir: Path,
    *,
    protocol: Any,
    thresholds: RobustnessThresholds = DEFAULT_THRESHOLDS,
) -> dict[str, Any]:
    protocol_ok, protocol_detail = frozen_protocol_ok(protocol)
    checks: list[dict[str, Any]] = [_check("robustness_protocol_frozen", protocol_ok, protocol_detail)]
    sections: dict[str, Any] = {}
    if not protocol_ok:
        return {
            "schema": "okx_robustness_screen_v1",
            "passed": False,
            "decision": "FAIL_STOP_NO_RESCUE",
            "checks": checks,
            "sections": sections,
        }

    try:
        falsification = _read_csv(
            artifact_dir / FALSIFICATION_FILENAME,
            {"test", "trial_id", "net_r", "profit_factor", "total_trades"},
        )
        neighborhood = _read_csv(
            artifact_dir / NEIGHBORHOOD_FILENAME,
            {"config_id", "is_primary", "distance", "net_r", "profit_factor", "total_trades"},
        )
        increment = _read_csv(
            artifact_dir / PORTFOLIO_INCREMENT_FILENAME,
            {
                "scenario",
                "profit_factor",
                "max_drawdown",
                "max_loss_streak",
                "effective_signal_count",
                "regime_coverage_count",
            },
        )
        sections["falsification"] = evaluate_falsification_trials(falsification, thresholds=thresholds)
        sections["parameter_neighborhood"] = evaluate_parameter_neighborhood(neighborhood, thresholds=thresholds)
        sections["portfolio_increment"] = evaluate_portfolio_increment(increment, thresholds=thresholds)
    except RobustnessEvidenceError as exc:
        checks.append(_check("robustness_evidence_complete", False, str(exc)))
        return {
            "schema": "okx_robustness_screen_v1",
            "passed": False,
            "decision": "FAIL_STOP_NO_RESCUE",
            "checks": checks,
            "sections": sections,
        }

    checks.append(_check("robustness_evidence_complete", True, "all evidence files parsed"))
    for section in sections.values():
        checks.extend(section["checks"])
    passed = all(bool(item["ok"]) for item in checks)
    return {
        "schema": "okx_robustness_screen_v1",
        "passed": passed,
        "decision": "PASS_TO_LOCKED_VALIDATION" if passed else "FAIL_STOP_NO_RESCUE",
        "checks": checks,
        "sections": sections,
    }
