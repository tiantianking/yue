from __future__ import annotations

"""Frozen membership-change-only rebalance evaluation for the momentum shadow."""

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

import research_sector_balanced_momentum as shared
from okx_signal_system.research.membership_change_rebalance import (
    RebalanceSimulation,
    exposure_summary,
    membership_path_agreement,
    simulate_rebalance_policy,
)
from okx_signal_system.research.sector_balanced_momentum import sector_capped_hysteresis_weights

PROTOCOL_PATH = PROJECT_ROOT / "config" / "research_protocols" / "momentum_membership_change_rebalance_v1.json"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "outputs" / "research" / "momentum_membership_change_rebalance_v1"


def _protocol_symbols(protocol: dict[str, Any]) -> list[str]:
    if protocol.get("status") != "LOCKED_BEFORE_PNL":
        raise ValueError("protocol must be locked before outcomes are opened")
    symbols = [str(value) for value in protocol.get("universe", {}).get("symbols", [])]
    if len(symbols) != 18 or len(set(symbols)) != 18:
        raise ValueError("frozen universe must contain 18 unique mature symbols")
    if int(protocol.get("execution_overlay", {}).get("parameter_count", -1)) != 0:
        raise ValueError("this execution overlay must remain parameter-free")
    return symbols


def _asset_return_matrix(entries: list[pd.Timestamp], open_prices: pd.DataFrame) -> np.ndarray:
    rows: list[np.ndarray] = []
    for start, end in zip(entries[:-1], entries[1:], strict=True):
        if start not in open_prices.index or end not in open_prices.index:
            raise ValueError(f"missing tradable open for interval {start} to {end}")
        start_values = open_prices.loc[start].to_numpy(dtype=float)
        end_values = open_prices.loc[end].to_numpy(dtype=float)
        if (start_values <= 0.0).any() or (end_values <= 0.0).any():
            raise ValueError("non-positive tradable open")
        rows.append(end_values / start_values - 1.0)
    return np.asarray(rows, dtype=float)


def _frame_from_simulation(
    entries: list[pd.Timestamp],
    simulation: RebalanceSimulation,
) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "start_utc": entries[:-1],
            "end_utc": entries[1:],
            "gross_return": simulation.gross_returns,
            "transaction_cost": simulation.transaction_costs,
            "funding_return": simulation.funding_returns,
            "net_return": simulation.net_returns,
            "turnover": simulation.turnovers,
            "gross_exposure": simulation.gross_exposures,
            "net_exposure": simulation.net_exposures,
            "rebalanced": simulation.rebalance_flags,
        }
    )


def _run_policy(
    entries: list[pd.Timestamp],
    targets: np.ndarray,
    open_prices: pd.DataFrame,
    funding_rates: np.ndarray,
    *,
    one_way_cost: float,
    adverse_funding_multiplier: float,
    mode: str,
) -> tuple[pd.DataFrame, RebalanceSimulation]:
    returns = _asset_return_matrix(entries, open_prices)
    simulation = simulate_rebalance_policy(
        targets,
        returns,
        funding_rates,
        one_way_cost=one_way_cost,
        adverse_funding_multiplier=adverse_funding_multiplier,
        mode=mode,  # type: ignore[arg-type]
    )
    return _frame_from_simulation(entries, simulation), simulation


def _falsification(
    entries: list[pd.Timestamp],
    targets: np.ndarray,
    h4_open: pd.DataFrame,
    delayed_open: pd.DataFrame,
    funding_rates: np.ndarray,
    delayed_funding_rates: np.ndarray,
    protocol: dict[str, Any],
) -> tuple[pd.DataFrame, dict[str, Any], pd.DataFrame]:
    costs = protocol["costs"]
    frozen = protocol["falsification"]
    base_cost = float(costs["one_way_baseline"])
    observed_frame, _ = _run_policy(
        entries,
        targets,
        h4_open,
        funding_rates,
        one_way_cost=base_cost,
        adverse_funding_multiplier=1.0,
        mode="membership_change_only",
    )
    reversed_frame, _ = _run_policy(
        entries,
        -targets,
        h4_open,
        funding_rates,
        one_way_cost=base_cost,
        adverse_funding_multiplier=1.0,
        mode="membership_change_only",
    )
    delayed_entries = [entry + pd.Timedelta(minutes=int(frozen["entry_delay_minutes"])) for entry in entries]
    delayed_frame, _ = _run_policy(
        delayed_entries,
        targets,
        delayed_open,
        delayed_funding_rates,
        one_way_cost=base_cost,
        adverse_funding_multiplier=1.0,
        mode="membership_change_only",
    )

    observed = shared._metrics(observed_frame)
    reversed_metrics = shared._metrics(reversed_frame)
    delayed = shared._metrics(delayed_frame)
    rows: list[dict[str, Any]] = [
        {
            "test": "observed",
            "trial_id": 0,
            "net_r": observed["net_r"],
            "profit_factor": observed["profit_factor"],
        },
        {
            "test": "direction_reversed",
            "trial_id": 0,
            "net_r": reversed_metrics["net_r"],
            "profit_factor": reversed_metrics["profit_factor"],
        },
        {
            "test": "entry_delay_15m",
            "trial_id": 0,
            "net_r": delayed["net_r"],
            "profit_factor": delayed["profit_factor"],
        },
    ]

    trials = int(frozen["random_time_trials"])
    rng = np.random.default_rng(int(frozen["random_seed"]))
    offsets = np.arange(1, len(targets) - 1, dtype=int)
    selected = rng.choice(offsets, size=trials, replace=len(offsets) < trials)
    random_net: list[float] = []
    for trial_id, offset in enumerate(selected, start=1):
        shifted = np.roll(targets, int(offset), axis=0)
        trial_frame, _ = _run_policy(
            entries,
            shifted,
            h4_open,
            funding_rates,
            one_way_cost=base_cost,
            adverse_funding_multiplier=1.0,
            mode="membership_change_only",
        )
        metrics = shared._metrics(trial_frame)
        value = float(metrics["net_r"] or 0.0)
        random_net.append(value)
        rows.append(
            {
                "test": "random_time",
                "trial_id": trial_id,
                "net_r": value,
                "profit_factor": metrics["profit_factor"],
            }
        )

    random_values = np.asarray(random_net, dtype=float)
    observed_net = float(observed["net_r"] or 0.0)
    q95 = float(np.quantile(random_values, 0.95))
    empirical_p = float((1 + np.count_nonzero(random_values >= observed_net)) / (1 + len(random_values)))
    reverse_gap = float(observed["profit_factor"] or 0.0) - float(reversed_metrics["profit_factor"] or 0.0)
    reverse_fraction = (
        float(reversed_metrics["net_r"] or 0.0) / observed_net if observed_net > 0.0 else float("inf")
    )
    delayed_retention = float(delayed["net_r"] or 0.0) / observed_net if observed_net > 0.0 else None
    gates = protocol["robustness_gates"]
    checks = {
        "observed_above_random_95th_percentile": observed_net > q95,
        "random_time_empirical_p_not_above_alpha": empirical_p <= float(gates["maximum_random_time_empirical_p"]),
        "reverse_profit_factor_gap_at_least_minimum": reverse_gap >= float(gates["minimum_reverse_pf_gap"]),
        "reverse_net_r_fraction_not_above_maximum": reverse_fraction <= float(gates["maximum_reverse_net_r_fraction"]),
        "delayed_profit_factor_at_least_one": float(delayed["profit_factor"] or 0.0) >= float(gates["minimum_delayed_profit_factor"]),
        "delayed_net_r_positive": float(delayed["net_r"] or 0.0) > 0.0,
        "delayed_net_r_retention_at_least_minimum": delayed_retention is not None
        and delayed_retention >= float(gates["minimum_delayed_net_r_retention"]),
    }
    return pd.DataFrame(rows), {
        "observed": observed,
        "direction_reversed": reversed_metrics,
        "entry_delay_15m": delayed,
        "random_time": {
            "trials": trials,
            "q95_net_r": q95,
            "empirical_p": empirical_p,
            "mean_net_r": float(random_values.mean()),
            "median_net_r": float(np.median(random_values)),
        },
        "comparison": {
            "reverse_profit_factor_gap": reverse_gap,
            "reverse_net_r_fraction": reverse_fraction,
            "delayed_net_r_retention": delayed_retention,
        },
        "checks": checks,
        "passed": bool(all(checks.values())),
    }, observed_frame


def _incremental_value(
    baseline_base: dict[str, Any],
    primary_base: dict[str, Any],
    baseline_stress: dict[str, Any],
    primary_stress: dict[str, Any],
) -> dict[str, Any]:
    protocol = shared._read_json(PROTOCOL_PATH)
    gates = protocol["incremental_value_gates"]
    material = gates["material_improvements"]
    turnover_reduction = 1.0 - float(primary_base["mean_turnover"] or 0.0) / float(
        baseline_base["mean_turnover"] or 1.0
    )
    transaction_cost_reduction = 1.0 - float(primary_base["transaction_cost"] or 0.0) / float(
        baseline_base["transaction_cost"] or 1.0
    )
    no_deterioration = {
        "pf_deterioration_within_limit": float(primary_base["profit_factor"] or 0.0)
        >= float(baseline_base["profit_factor"] or 0.0) - float(gates["maximum_pf_deterioration"]),
        "drawdown_deterioration_within_limit": abs(float(primary_base["maximum_drawdown"] or 0.0))
        <= abs(float(baseline_base["maximum_drawdown"] or 0.0))
        + float(gates["maximum_drawdown_deterioration"]),
        "loss_streak_increase_within_limit": int(primary_base["maximum_loss_streak"] or 0)
        <= int(baseline_base["maximum_loss_streak"] or 0) + int(gates["maximum_loss_streak_increase"]),
        "stress_pf_not_below_baseline": float(primary_stress["profit_factor"] or 0.0)
        >= float(baseline_stress["profit_factor"] or 0.0) + float(gates["minimum_stress_pf_improvement"]),
        "turnover_reduction_at_least_minimum": turnover_reduction
        >= float(gates["minimum_turnover_reduction"]),
        "transaction_cost_reduction_at_least_minimum": transaction_cost_reduction
        >= float(gates["minimum_transaction_cost_reduction"]),
    }
    improvements = {
        "profit_factor_improved": float(primary_base["profit_factor"] or 0.0)
        >= float(baseline_base["profit_factor"] or 0.0) + float(material["profit_factor"]),
        "maximum_drawdown_improved": abs(float(primary_base["maximum_drawdown"] or 0.0))
        <= abs(float(baseline_base["maximum_drawdown"] or 0.0)) - float(material["maximum_drawdown"]),
        "maximum_loss_streak_improved": int(primary_base["maximum_loss_streak"] or 0)
        <= int(baseline_base["maximum_loss_streak"] or 0) - int(material["maximum_loss_streak"]),
        "stress_profit_factor_improved": float(primary_stress["profit_factor"] or 0.0)
        >= float(baseline_stress["profit_factor"] or 0.0) + float(material["stress_profit_factor"]),
        "turnover_improved": turnover_reduction >= float(material["turnover_fraction"]),
        "transaction_cost_improved": transaction_cost_reduction
        >= float(material["transaction_cost_fraction"]),
    }
    improvement_count = int(sum(improvements.values()))
    return {
        "turnover_reduction": turnover_reduction,
        "transaction_cost_reduction": transaction_cost_reduction,
        "no_deterioration_checks": no_deterioration,
        "improvement_checks": improvements,
        "material_improvement_count": improvement_count,
        "passed": bool(
            all(no_deterioration.values())
            and improvement_count >= int(gates["minimum_material_improvements"])
        ),
    }


def _markdown(result: dict[str, Any]) -> str:
    baseline = result["performance"]["baseline_base"]
    primary = result["performance"]["primary_base"]
    baseline_stress = result["performance"]["baseline_stress"]
    primary_stress = result["performance"]["primary_stress"]
    exposure = result["structural"]["exposure"]
    failed = [name for name, group in result["gate_groups"].items() if not group["passed"]]
    lines = [
        "# 14日动量仅成员变化时再平衡 V1：研究结论",
        "",
        f"最终状态：`{result['decision']}`",
        "",
        "本轮保持14日动量、4入6出和全部成员路径不变。基准每天把持仓恢复为等权；主版本只有在多空成员发生变化时才恢复等权，其余日子保留自然漂移且不交易。",
        "",
        "## 核心结果",
        "",
        f"- 每日等权基准：PF {float(baseline['profit_factor'] or 0.0):.4f}，总收益 {float(baseline['total_return'] or 0.0):.2%}，最大回撤 {float(baseline['maximum_drawdown'] or 0.0):.2%}，平均换手 {float(baseline['mean_turnover'] or 0.0):.4f}；",
        f"- 成员变化再平衡：PF {float(primary['profit_factor'] or 0.0):.4f}，总收益 {float(primary['total_return'] or 0.0):.2%}，最大回撤 {float(primary['maximum_drawdown'] or 0.0):.2%}，平均换手 {float(primary['mean_turnover'] or 0.0):.4f}；",
        f"- 压力PF：基准 {float(baseline_stress['profit_factor'] or 0.0):.4f}，主版本 {float(primary_stress['profit_factor'] or 0.0):.4f}；",
        f"- 换手降低 {float(result['incremental_value']['turnover_reduction']):.2%}，交易成本降低 {float(result['incremental_value']['transaction_cost_reduction']):.2%}；",
        f"- 不交易决策占比 {float(exposure['no_trade_decision_fraction']):.2%}，平均绝对净敞口 {float(exposure['mean_absolute_net_exposure']):.2%}，95分位绝对净敞口 {float(exposure['p95_absolute_net_exposure']):.2%}。",
        "",
        "## 门禁",
        "",
    ]
    for name, group in result["gate_groups"].items():
        lines.append(f"- {name}: {'通过' if group['passed'] else '失败'}")
    lines.extend(
        [
            "",
            "## 决策",
            "",
            (
                "全部冻结门禁通过，只允许作为独立研究影子继续前向观察；仍不得视为A级。"
                if not failed
                else "失败门禁：" + "、".join(failed) + "。永久归档，禁止增加漂移阈值、定期重平衡或其他事后营救条件。"
            ),
            "",
            "生产系统影响：`NONE`  ",
            "自动下单影响：`NONE`",
            "",
        ]
    )
    return "\n".join(lines)


def _archive_failure(output_dir: Path, result: dict[str, Any]) -> Path:
    archive_dir = Path.home() / "Desktop" / "失败策略" / str(result["protocol_id"])
    archive_dir.mkdir(parents=True, exist_ok=True)
    for path in output_dir.iterdir():
        if path.is_file():
            shutil.copy2(path, archive_dir / path.name)
    shutil.copy2(PROTOCOL_PATH, archive_dir / PROTOCOL_PATH.name)
    shared._write_json(
        archive_dir / "failure_summary.json",
        {
            "candidate_id": result["protocol_id"],
            "status": result["decision"],
            "failed_stage": "historical_execution_overlay_validation",
            "pnl_opened": True,
            "independent_alpha_claim": False,
            "failed_gate_groups": [
                name for name, group in result["gate_groups"].items() if not group["passed"]
            ],
            "no_rescue": True,
            "production_effect": "NONE",
        },
    )
    (archive_dir / "失败说明.md").write_text(_markdown(result), encoding="utf-8")
    return archive_dir


def run(output_dir: Path) -> dict[str, Any]:
    protocol = shared._read_json(PROTOCOL_PATH)
    symbols = _protocol_symbols(protocol)
    panels = shared.common.load_panels()
    entries, mappings, _, _ = shared._build_signal_inputs(panels, symbols)
    if len(entries) < 500:
        raise ValueError(f"insufficient daily signals: {len(entries)}")

    unique_sector = {symbol: symbol for symbol in symbols}
    targets = sector_capped_hysteresis_weights(
        mappings,
        symbols,
        unique_sector,
        top_n=4,
        exit_rank=6,
        max_per_sector=4,
    )
    funding, funding_coverage = shared._load_funding(symbols)
    funding_rates = shared._funding_rate_matrix(entries, symbols, funding)
    delayed_entries = [entry + pd.Timedelta(minutes=15) for entry in entries]
    delayed_funding_rates = shared._funding_rate_matrix(delayed_entries, symbols, funding)
    h4_open = panels.h4_open.loc[:, symbols]
    delayed_open = panels.m15_open.loc[:, symbols]
    base_cost = float(protocol["costs"]["one_way_baseline"])
    stress_cost = float(protocol["costs"]["one_way_stress"])

    baseline_frame, baseline_sim = _run_policy(
        entries,
        targets,
        h4_open,
        funding_rates,
        one_way_cost=base_cost,
        adverse_funding_multiplier=1.0,
        mode="daily_equal_reset",
    )
    primary_frame, primary_sim = _run_policy(
        entries,
        targets,
        h4_open,
        funding_rates,
        one_way_cost=base_cost,
        adverse_funding_multiplier=1.0,
        mode="membership_change_only",
    )
    baseline_stress_frame, _ = _run_policy(
        entries,
        targets,
        h4_open,
        funding_rates,
        one_way_cost=stress_cost,
        adverse_funding_multiplier=2.0,
        mode="daily_equal_reset",
    )
    primary_stress_frame, _ = _run_policy(
        entries,
        targets,
        h4_open,
        funding_rates,
        one_way_cost=stress_cost,
        adverse_funding_multiplier=2.0,
        mode="membership_change_only",
    )

    baseline_metrics = shared._metrics(baseline_frame)
    primary_metrics = shared._metrics(primary_frame)
    baseline_stress = shared._metrics(baseline_stress_frame)
    primary_stress = shared._metrics(primary_stress_frame)
    segments = shared._segment_metrics(primary_frame)
    exposure = exposure_summary(primary_sim)
    membership_agreement = membership_path_agreement(targets[:-1], primary_sim.start_weights)
    turnover_reduction = 1.0 - float(primary_metrics["mean_turnover"] or 0.0) / float(
        baseline_metrics["mean_turnover"] or 1.0
    )
    transaction_cost_reduction = 1.0 - float(primary_metrics["transaction_cost"] or 0.0) / float(
        baseline_metrics["transaction_cost"] or 1.0
    )

    structural_thresholds = protocol["structural_gates"]
    structural_checks = {
        "no_trade_fraction_at_least_minimum": float(exposure["no_trade_decision_fraction"])
        >= float(structural_thresholds["minimum_no_trade_decision_fraction"]),
        "turnover_reduction_at_least_minimum": turnover_reduction
        >= float(structural_thresholds["minimum_turnover_reduction_vs_daily_reset"]),
        "transaction_cost_reduction_at_least_minimum": transaction_cost_reduction
        >= float(structural_thresholds["minimum_transaction_cost_reduction_vs_daily_reset"]),
        "mean_absolute_net_exposure_within_limit": float(exposure["mean_absolute_net_exposure"])
        <= float(structural_thresholds["maximum_mean_absolute_net_exposure"]),
        "p95_absolute_net_exposure_within_limit": float(exposure["p95_absolute_net_exposure"])
        <= float(structural_thresholds["maximum_p95_absolute_net_exposure"]),
        "maximum_absolute_net_exposure_within_limit": float(exposure["maximum_absolute_net_exposure"])
        <= float(structural_thresholds["maximum_single_interval_absolute_net_exposure"]),
        "p05_gross_exposure_at_least_minimum": float(exposure["p05_gross_exposure"])
        >= float(structural_thresholds["minimum_p05_gross_exposure"]),
        "p95_gross_exposure_within_limit": float(exposure["p95_gross_exposure"])
        <= float(structural_thresholds["maximum_p95_gross_exposure"]),
        "membership_path_agreement_exact": membership_agreement
        >= float(structural_thresholds["minimum_membership_path_agreement"]),
        "daily_baseline_rebalances_every_interval": bool(np.all(baseline_sim.rebalance_flags)),
    }
    structural = {
        "exposure": exposure,
        "membership_path_agreement": membership_agreement,
        "turnover_reduction_vs_daily_reset": turnover_reduction,
        "transaction_cost_reduction_vs_daily_reset": transaction_cost_reduction,
        "primary_rebalance_count": int(np.count_nonzero(primary_sim.rebalance_flags)),
        "baseline_rebalance_count": int(np.count_nonzero(baseline_sim.rebalance_flags)),
        "checks": structural_checks,
        "passed": bool(all(structural_checks.values())),
    }

    historical_thresholds = protocol["historical_gates"]
    positive_segments = sum(float(item.get("mean") or 0.0) > 0.0 for item in segments.values())
    baseline_total = float(baseline_metrics["total_return"] or 0.0)
    total_return_retention = (
        float(primary_metrics["total_return"] or 0.0) / baseline_total if baseline_total > 0.0 else None
    )
    historical_checks = {
        "base_profit_factor_at_least_minimum": float(primary_metrics["profit_factor"] or 0.0)
        >= float(historical_thresholds["minimum_base_profit_factor"]),
        "stress_profit_factor_at_least_minimum": float(primary_stress["profit_factor"] or 0.0)
        >= float(historical_thresholds["minimum_stress_profit_factor"]),
        "base_pf_loss_vs_baseline_within_limit": float(primary_metrics["profit_factor"] or 0.0)
        >= float(baseline_metrics["profit_factor"] or 0.0)
        - float(historical_thresholds["maximum_base_pf_loss_vs_baseline"]),
        "drawdown_increase_vs_baseline_within_limit": abs(float(primary_metrics["maximum_drawdown"] or 0.0))
        <= abs(float(baseline_metrics["maximum_drawdown"] or 0.0))
        + float(historical_thresholds["maximum_drawdown_increase_vs_baseline"]),
        "total_return_retention_at_least_minimum": total_return_retention is not None
        and total_return_retention >= float(historical_thresholds["minimum_total_return_retention_vs_baseline"]),
        "positive_in_at_least_two_segments": positive_segments
        >= int(historical_thresholds["minimum_positive_chronological_segments"]),
    }
    historical = {
        "positive_segments": positive_segments,
        "total_return_retention_vs_baseline": total_return_retention,
        "segments": segments,
        "checks": historical_checks,
        "passed": bool(all(historical_checks.values())),
    }

    falsification_frame, falsification, observed_frame = _falsification(
        entries,
        targets,
        h4_open,
        delayed_open,
        funding_rates,
        delayed_funding_rates,
        protocol,
    )
    incremental = _incremental_value(
        baseline_metrics,
        primary_metrics,
        baseline_stress,
        primary_stress,
    )
    implementation_checks = {
        "parameter_count_is_zero": int(protocol["execution_overlay"]["parameter_count"]) == 0,
        "target_membership_path_unchanged": membership_agreement == 1.0,
        "first_start_weights_equal_frozen_target": bool(
            np.allclose(primary_sim.start_weights[0], targets[0])
        ),
        "terminal_liquidation_included": float(primary_sim.turnovers[-1]) > 0.0,
        "all_values_finite": bool(
            np.isfinite(primary_sim.net_returns).all()
            and np.isfinite(primary_sim.start_weights).all()
            and np.isfinite(primary_sim.end_weights).all()
        ),
    }
    implementation = {
        "checks": implementation_checks,
        "passed": bool(all(implementation_checks.values())),
    }

    gate_groups = {
        "structural_and_exposure": {"passed": structural["passed"], "checks": structural["checks"]},
        "historical_cost_and_segments": {"passed": historical["passed"], "checks": historical["checks"]},
        "falsification": {"passed": falsification["passed"], "checks": falsification["checks"]},
        "implementation_invariance": {"passed": implementation["passed"], "checks": implementation["checks"]},
        "incremental_value": {
            "passed": incremental["passed"],
            "checks": {
                **incremental["no_deterioration_checks"],
                "minimum_material_improvement_count": int(incremental["material_improvement_count"])
                >= int(protocol["incremental_value_gates"]["minimum_material_improvements"]),
            },
        },
    }
    passed = bool(all(group["passed"] for group in gate_groups.values()))
    result: dict[str, Any] = {
        "schema": "momentum_membership_change_rebalance_evaluation_v1",
        "protocol_id": protocol["protocol_id"],
        "protocol_locked_before_pnl": True,
        "outcomes_opened": True,
        "history_cutoff_utc": shared.OPENED_HISTORY_CUTOFF,
        "signal_count": len(entries),
        "universe": symbols,
        "funding_coverage": funding_coverage,
        "performance": {
            "baseline_base": baseline_metrics,
            "primary_base": primary_metrics,
            "baseline_stress": baseline_stress,
            "primary_stress": primary_stress,
        },
        "structural": structural,
        "historical": historical,
        "falsification": falsification,
        "implementation_invariance": implementation,
        "incremental_value": incremental,
        "gate_groups": gate_groups,
        "decision": "HISTORICALLY_SUPPORTED_RESEARCH_SHADOW_ONLY" if passed else "REJECT_AND_ARCHIVE_NO_RESCUE",
        "independent_alpha_claim": False,
        "new_h_number": None,
        "formal_a_allowed": False,
        "production_effect": "NONE",
        "automatic_ordering": False,
        "prohibitions": protocol["prohibitions"],
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    falsification_frame.to_csv(output_dir / "falsification_trials.csv", index=False)
    observed_frame.to_csv(output_dir / "primary_interval_returns.csv", index=False)
    pd.DataFrame(primary_sim.start_weights, columns=symbols).assign(
        start_utc=entries[:-1],
        rebalanced=primary_sim.rebalance_flags,
    ).to_csv(output_dir / "primary_start_weights.csv", index=False)
    shared._write_json(output_dir / "result.json", result)
    (output_dir / "RESULTS_CN.md").write_text(_markdown(result), encoding="utf-8")
    shutil.copy2(PROTOCOL_PATH, output_dir / PROTOCOL_PATH.name)
    shared._write_json(
        output_dir / "SHA256SUMS.json",
        {
            path.name: shared._sha256(path)
            for path in sorted(output_dir.iterdir())
            if path.is_file() and path.name != "SHA256SUMS.json"
        },
    )
    if not passed:
        result["failure_archive"] = str(_archive_failure(output_dir, result))
        shared._write_json(output_dir / "result.json", result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    result = run(args.output_dir.resolve())
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, default=shared._json_default))
    else:
        print(
            json.dumps(
                {
                    "protocol_id": result["protocol_id"],
                    "decision": result["decision"],
                    "signal_count": result["signal_count"],
                    "failed_gate_groups": [
                        name for name, group in result["gate_groups"].items() if not group["passed"]
                    ],
                    "failure_archive": result.get("failure_archive"),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
