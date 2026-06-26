from __future__ import annotations

"""Frozen liquidity-admission overlay evaluation for the existing momentum shadow."""

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
from okx_signal_system.research.liquidity_admission_momentum import (
    eligible_set_fraction,
    liquidity_admission_hysteresis_weights,
)
from okx_signal_system.research.sector_balanced_momentum import (
    maximum_symbol_slot_share,
    sector_capped_hysteresis_weights,
)

PROTOCOL_PATH = PROJECT_ROOT / "config" / "research_protocols" / "momentum_liquidity_admission_v1.json"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "outputs" / "research" / "momentum_liquidity_admission_v1"


def _protocol_symbols(protocol: dict[str, Any]) -> list[str]:
    if protocol.get("status") != "LOCKED_BEFORE_PNL":
        raise ValueError("protocol must be locked before outcomes are opened")
    symbols = [str(value) for value in protocol.get("universe", {}).get("symbols", [])]
    if len(symbols) != 18 or len(set(symbols)) != 18:
        raise ValueError("frozen universe must contain 18 unique mature symbols")
    return symbols


def _h4_quote_volume(symbols: list[str]) -> pd.DataFrame:
    frames: dict[str, pd.DataFrame] = {}
    common_index: pd.DatetimeIndex | None = None
    for symbol in symbols:
        raw = shared.common.load_symbol_15m(symbol)
        h4 = shared.common.strict_resample(raw, "4h", 16)
        frames[symbol] = h4
        common_index = h4.index if common_index is None else common_index.intersection(h4.index)
    if common_index is None or common_index.empty:
        raise ValueError("empty common quote-volume index")
    common_index = common_index.sort_values()
    return pd.DataFrame(
        {symbol: frames[symbol]["volume_quote"].reindex(common_index) for symbol in symbols},
        index=common_index,
        dtype=float,
    )


def _liquidity_inputs(
    entries: list[pd.Timestamp],
    symbols: list[str],
    *,
    lookback_bars: int,
    shift_bars: int,
) -> tuple[list[dict[str, float]], pd.DataFrame]:
    quote_volume = _h4_quote_volume(symbols)
    score = quote_volume.rolling(lookback_bars, min_periods=lookback_bars).mean().shift(shift_bars)
    mappings: list[dict[str, float]] = []
    for entry in entries:
        signal_time = entry - pd.Timedelta(hours=4)
        if signal_time not in score.index:
            raise ValueError(f"missing liquidity score at {signal_time}")
        current = score.loc[signal_time]
        if current.isna().any():
            raise ValueError(f"incomplete causal liquidity score at {signal_time}")
        mappings.append({symbol: float(current[symbol]) for symbol in symbols})
    return mappings, score


def _entry_eligibility_violations(
    weights: np.ndarray,
    liquidity_mappings: list[dict[str, float]],
    symbols: list[str],
    *,
    eligible_count: int,
) -> int:
    previous = np.zeros(len(symbols), dtype=float)
    violations = 0
    for row, mapping in zip(weights, liquidity_mappings, strict=True):
        eligible = set(
            pd.Series(mapping, dtype=float)
            .reindex(symbols)
            .sort_values(ascending=False, kind="mergesort")
            .head(eligible_count)
            .index
        )
        new_members = [
            symbols[index]
            for index in range(len(symbols))
            if abs(row[index]) > 1e-12 and abs(previous[index]) <= 1e-12
        ]
        violations += sum(symbol not in eligible for symbol in new_members)
        previous = row
    return violations


def _parameter_neighborhood(
    entries: list[pd.Timestamp],
    variants: dict[str, np.ndarray],
    h4_open: pd.DataFrame,
    funding_rates: np.ndarray,
    protocol: dict[str, Any],
) -> tuple[pd.DataFrame, dict[str, Any]]:
    declarations = {item["config_id"]: item for item in protocol["parameter_neighborhood"]}
    rows: list[dict[str, Any]] = []
    for config_id, weights in variants.items():
        metrics = shared._metrics(
            shared._simulate(
                entries,
                weights,
                h4_open,
                funding_rates,
                one_way_cost=float(protocol["costs"]["one_way_baseline"]),
                adverse_funding_multiplier=1.0,
            )
        )
        declaration = declarations[config_id]
        rows.append(
            {
                "config_id": config_id,
                "is_primary": bool(declaration["is_primary"]),
                "entry_eligible_count": int(declaration["entry_eligible_count"]),
                "net_r": metrics["net_r"],
                "profit_factor": metrics["profit_factor"],
                "mean_turnover": metrics["mean_turnover"],
                "total_trades": metrics["periods"],
            }
        )
    frame = pd.DataFrame(rows)
    neighbors = frame.loc[~frame["is_primary"]]
    primary_pf = float(frame.loc[frame["is_primary"], "profit_factor"].iloc[0])
    positive_ratio = float((neighbors["net_r"] > 0.0).mean())
    median_pf = float(neighbors["profit_factor"].median())
    gates = protocol["robustness_gates"]
    checks = {
        "two_frozen_neighbors_present": len(neighbors) == 2,
        "positive_neighbor_ratio_at_least_minimum": positive_ratio
        >= float(gates["minimum_positive_neighbor_ratio"]),
        "neighbor_median_profit_factor_at_least_one": median_pf
        >= float(gates["minimum_neighbor_median_profit_factor"]),
        "primary_pf_not_above_twice_neighbor_median": primary_pf
        <= float(gates["maximum_primary_to_neighbor_median_pf_ratio"]) * median_pf,
    }
    return frame, {
        "positive_neighbor_ratio": positive_ratio,
        "neighbor_median_profit_factor": median_pf,
        "primary_profit_factor": primary_pf,
        "checks": checks,
        "passed": bool(all(checks.values())),
    }


def _portfolio_increment(
    entries: list[pd.Timestamp],
    baseline: np.ndarray,
    primary: np.ndarray,
    h4_open: pd.DataFrame,
    funding_rates: np.ndarray,
    regime_labels: dict[pd.Timestamp, str],
    protocol: dict[str, Any],
) -> tuple[pd.DataFrame, dict[str, Any]]:
    scenarios = {"baseline": baseline, "combined": 0.5 * baseline + 0.5 * primary}
    rows: list[dict[str, Any]] = []
    metrics_by_name: dict[str, dict[str, Any]] = {}
    for name, weights in scenarios.items():
        frame = shared._simulate(
            entries,
            weights,
            h4_open,
            funding_rates,
            one_way_cost=float(protocol["costs"]["one_way_baseline"]),
            adverse_funding_multiplier=1.0,
        )
        metrics = shared._metrics(frame)
        metrics["effective_signal_count"] = shared._effective_signal_count(weights)
        metrics["regime_coverage_count"] = shared._positive_regime_count(frame, regime_labels)
        metrics_by_name[name] = metrics
        rows.append(
            {
                "scenario": name,
                "profit_factor": metrics["profit_factor"],
                "max_drawdown": metrics["maximum_drawdown"],
                "max_loss_streak": metrics["maximum_loss_streak"],
                "effective_signal_count": metrics["effective_signal_count"],
                "regime_coverage_count": metrics["regime_coverage_count"],
                "mean_turnover": metrics["mean_turnover"],
            }
        )

    base = metrics_by_name["baseline"]
    combo = metrics_by_name["combined"]
    gates = protocol["portfolio_increment_gates"]
    required = gates["required_improvements_any"]
    no_deterioration = {
        "pf_deterioration_within_limit": float(combo["profit_factor"] or 0.0)
        >= float(base["profit_factor"] or 0.0) - float(gates["maximum_pf_deterioration"]),
        "drawdown_deterioration_within_limit": abs(float(combo["maximum_drawdown"] or 0.0))
        <= abs(float(base["maximum_drawdown"] or 0.0)) + float(gates["maximum_drawdown_deterioration"]),
        "loss_streak_increase_within_limit": int(combo["maximum_loss_streak"] or 0)
        <= int(base["maximum_loss_streak"] or 0) + int(gates["maximum_loss_streak_increase"]),
    }
    minimum_effective = max(
        int(base["effective_signal_count"] * (1.0 + float(required["effective_signal_fraction"]))),
        int(base["effective_signal_count"] + int(required["effective_signal_absolute"])),
    )
    turnover_reduction = 1.0 - float(combo["mean_turnover"] or 0.0) / float(base["mean_turnover"] or 1.0)
    improvements = {
        "profit_factor_improved": float(combo["profit_factor"] or 0.0)
        >= float(base["profit_factor"] or 0.0) + float(required["profit_factor"]),
        "maximum_drawdown_improved": abs(float(combo["maximum_drawdown"] or 0.0))
        <= abs(float(base["maximum_drawdown"] or 0.0)) - float(required["maximum_drawdown"]),
        "maximum_loss_streak_improved": int(combo["maximum_loss_streak"] or 0)
        <= int(base["maximum_loss_streak"] or 0) - int(required["maximum_loss_streak"]),
        "effective_signal_count_improved": int(combo["effective_signal_count"]) >= minimum_effective,
        "positive_regime_count_improved": int(combo["regime_coverage_count"])
        >= int(base["regime_coverage_count"]) + int(required["positive_regime_count"]),
        "mean_turnover_improved": turnover_reduction >= float(required["mean_turnover_fraction"]),
    }
    return pd.DataFrame(rows), {
        "metrics": metrics_by_name,
        "turnover_reduction": turnover_reduction,
        "no_deterioration_checks": no_deterioration,
        "improvement_checks": improvements,
        "passed": bool(all(no_deterioration.values()) and any(improvements.values())),
    }


def _markdown(result: dict[str, Any]) -> str:
    baseline = result["performance"]["baseline_base"]
    primary = result["performance"]["primary_base"]
    stress = result["performance"]["primary_stress"]
    failed = [name for name, group in result["gate_groups"].items() if not group["passed"]]
    lines = [
        "# 14日动量流动性入场覆盖层 V1：研究结论",
        "",
        f"最终状态：`{result['decision']}`",
        "",
        "本轮不是新Alpha。它保持14日动量、4入6出、方向和等权仓位不变，只让过去30日OKX成交额最高的12个币获得新入场资格；流动性变化不得强制卖出已有成员。",
        "",
        "## 核心结果",
        "",
        f"- 原4入6出：PF {float(baseline['profit_factor'] or 0.0):.4f}，总收益 {float(baseline['total_return'] or 0.0):.2%}，最大回撤 {float(baseline['maximum_drawdown'] or 0.0):.2%}，平均换手 {float(baseline['mean_turnover'] or 0.0):.4f}；",
        f"- 流动性入场版：PF {float(primary['profit_factor'] or 0.0):.4f}，总收益 {float(primary['total_return'] or 0.0):.2%}，最大回撤 {float(primary['maximum_drawdown'] or 0.0):.2%}，平均换手 {float(primary['mean_turnover'] or 0.0):.4f}；",
        f"- 压力成本：PF {float(stress['profit_factor'] or 0.0):.4f}，总收益 {float(stress['total_return'] or 0.0):.2%}；",
        f"- 相对基准换手降低 {float(result['structural']['turnover_reduction_vs_baseline']):.2%}，覆盖层实际改变组合的时点占比 {float(result['structural']['overlay_binding_fraction']):.2%}。",
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
                "全部冻结门禁通过，只允许作为研究影子继续前向观察；仍不得视为A级。"
                if not failed
                else "失败门禁：" + "、".join(failed) + "。永久归档，禁止修改流动性窗口、入场集合、方向、持有期、币种或成本营救。"
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
            "failed_stage": "historical_robustness_and_incremental_value",
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
    entries, momentum_mappings, _, _ = shared._build_signal_inputs(panels, symbols)
    if len(entries) < 500:
        raise ValueError(f"insufficient daily signals: {len(entries)}")

    overlay = protocol["liquidity_overlay"]
    liquidity_mappings, _ = _liquidity_inputs(
        entries,
        symbols,
        lookback_bars=int(overlay["lookback_bars_4h"]),
        shift_bars=int(overlay["causal_shift_bars"]),
    )
    unique_sector = {symbol: symbol for symbol in symbols}
    baseline = sector_capped_hysteresis_weights(
        momentum_mappings,
        symbols,
        unique_sector,
        top_n=4,
        exit_rank=6,
        max_per_sector=4,
    )
    variants = {
        declaration["config_id"]: liquidity_admission_hysteresis_weights(
            momentum_mappings,
            liquidity_mappings,
            symbols,
            top_n=4,
            exit_rank=6,
            eligible_count=int(declaration["entry_eligible_count"]),
        )
        for declaration in protocol["parameter_neighborhood"]
    }
    primary = variants["primary_liquid12_hysteresis6"]

    funding, funding_coverage = shared._load_funding(symbols)
    funding_rates = shared._funding_rate_matrix(entries, symbols, funding)
    delayed_entries = [entry + pd.Timedelta(minutes=15) for entry in entries]
    delayed_funding_rates = shared._funding_rate_matrix(delayed_entries, symbols, funding)
    h4_open = panels.h4_open.loc[:, symbols]
    delayed_open = panels.m15_open.loc[:, symbols]

    base_cost = float(protocol["costs"]["one_way_baseline"])
    stress_cost = float(protocol["costs"]["one_way_stress"])
    baseline_frame = shared._simulate(
        entries, baseline, h4_open, funding_rates, one_way_cost=base_cost, adverse_funding_multiplier=1.0
    )
    primary_frame = shared._simulate(
        entries, primary, h4_open, funding_rates, one_way_cost=base_cost, adverse_funding_multiplier=1.0
    )
    stress_frame = shared._simulate(
        entries, primary, h4_open, funding_rates, one_way_cost=stress_cost, adverse_funding_multiplier=2.0
    )
    baseline_metrics = shared._metrics(baseline_frame)
    primary_metrics = shared._metrics(primary_frame)
    stress_metrics = shared._metrics(stress_frame)
    segments = shared._segment_metrics(primary_frame)

    binding_fraction = float(np.mean(np.any(np.abs(primary - baseline) > 1e-12, axis=1)))
    turnover_reduction = 1.0 - float(primary_metrics["mean_turnover"] or 0.0) / float(
        baseline_metrics["mean_turnover"] or 1.0
    )
    symbol_share = maximum_symbol_slot_share(primary)
    complete_fraction = eligible_set_fraction(
        liquidity_mappings, symbols, eligible_count=int(overlay["primary_entry_eligible_count"])
    )
    entry_violations = _entry_eligibility_violations(
        primary,
        liquidity_mappings,
        symbols,
        eligible_count=int(overlay["primary_entry_eligible_count"]),
    )
    long_short_overlap = int(
        max(
            np.count_nonzero((row > 0.0) & (row < 0.0))
            for row in primary
        )
    )
    structural_thresholds = protocol["structural_gates"]
    structural_checks = {
        "overlay_binds_often_enough": binding_fraction
        >= float(structural_thresholds["minimum_liquidity_overlay_binding_fraction"]),
        "turnover_reduction_at_least_minimum": turnover_reduction
        >= float(structural_thresholds["minimum_turnover_reduction_vs_baseline"]),
        "single_symbol_slot_share_within_cap": float(symbol_share["maximum"])
        <= float(structural_thresholds["maximum_single_symbol_slot_share"]),
        "eligible_set_complete": complete_fraction
        >= float(structural_thresholds["minimum_complete_eligible_set_fraction"]),
        "new_entries_obey_liquidity_gate": entry_violations == 0,
        "long_short_overlap_within_limit": long_short_overlap
        <= int(structural_thresholds["maximum_long_short_overlap"]),
        "all_targets_market_neutral": bool(np.allclose(primary.sum(axis=1), 0.0)),
        "all_targets_unit_gross": bool(np.allclose(np.abs(primary).sum(axis=1), 1.0)),
    }
    structural = {
        "overlay_binding_fraction": binding_fraction,
        "turnover_reduction_vs_baseline": turnover_reduction,
        "maximum_symbol_slot_share": symbol_share,
        "complete_eligible_set_fraction": complete_fraction,
        "entry_eligibility_violations": entry_violations,
        "maximum_long_short_overlap": long_short_overlap,
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
        "stress_profit_factor_at_least_minimum": float(stress_metrics["profit_factor"] or 0.0)
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

    falsification_frame, falsification, observed_frame, _ = shared._falsification_rows(
        entries,
        primary,
        h4_open,
        delayed_open,
        funding_rates,
        delayed_funding_rates,
        protocol,
    )
    neighborhood_frame, neighborhood = _parameter_neighborhood(
        entries, variants, h4_open, funding_rates, protocol
    )
    regimes = shared._regime_labels(panels, entries, symbols)
    portfolio_frame, portfolio = _portfolio_increment(
        entries, baseline, primary, h4_open, funding_rates, regimes, protocol
    )

    gate_groups = {
        "structural": {"passed": structural["passed"], "checks": structural["checks"]},
        "historical_cost_and_segments": {"passed": historical["passed"], "checks": historical["checks"]},
        "falsification": {"passed": falsification["passed"], "checks": falsification["checks"]},
        "parameter_neighborhood": {"passed": neighborhood["passed"], "checks": neighborhood["checks"]},
        "portfolio_increment": {
            "passed": portfolio["passed"],
            "checks": {
                **portfolio["no_deterioration_checks"],
                "at_least_one_material_improvement": any(portfolio["improvement_checks"].values()),
            },
        },
    }
    passed = bool(all(group["passed"] for group in gate_groups.values()))
    result: dict[str, Any] = {
        "schema": "momentum_liquidity_admission_evaluation_v1",
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
            "primary_stress": stress_metrics,
        },
        "structural": structural,
        "historical": historical,
        "falsification": falsification,
        "parameter_neighborhood": neighborhood,
        "portfolio_increment": portfolio,
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
    neighborhood_frame.to_csv(output_dir / "parameter_neighborhood.csv", index=False)
    portfolio_frame.to_csv(output_dir / "portfolio_increment.csv", index=False)
    observed_frame.to_csv(output_dir / "primary_interval_returns.csv", index=False)
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
