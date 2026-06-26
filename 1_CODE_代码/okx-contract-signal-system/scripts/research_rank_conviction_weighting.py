from __future__ import annotations

"""Frozen rank-conviction weighting evaluation for the existing momentum shadow."""

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
from okx_signal_system.research.rank_conviction_weighting import rank_conviction_weight_path
from okx_signal_system.research.sector_balanced_momentum import sector_capped_hysteresis_weights

PROTOCOL_PATH = PROJECT_ROOT / "config" / "research_protocols" / "momentum_rank_conviction_v1.json"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "outputs" / "research" / "momentum_rank_conviction_v1"


def _protocol_symbols(protocol: dict[str, Any]) -> list[str]:
    if protocol.get("status") != "LOCKED_BEFORE_PNL":
        raise ValueError("protocol must be locked before outcomes are opened")
    symbols = [str(value) for value in protocol.get("universe", {}).get("symbols", [])]
    if len(symbols) != 18 or len(set(symbols)) != 18:
        raise ValueError("frozen universe must contain 18 unique mature symbols")
    return symbols


def _score_mappings(
    entries: list[pd.Timestamp],
    score_frame: pd.DataFrame,
    symbols: list[str],
) -> list[dict[str, float]]:
    mappings: list[dict[str, float]] = []
    for entry in entries:
        signal_time = entry - pd.Timedelta(hours=4)
        row = score_frame.loc[signal_time, symbols]
        if row.isna().any():
            raise ValueError(f"non-finite score row at {signal_time}")
        mappings.append({symbol: float(row[symbol]) for symbol in symbols})
    return mappings


def _markdown(result: dict[str, Any]) -> str:
    baseline = result["performance"]["baseline_base"]
    primary = result["performance"]["primary_base"]
    stress = result["performance"]["primary_stress"]
    failed = [name for name, group in result["gate_groups"].items() if not group["passed"]]
    lines = [
        "# 14日动量排名强度分层权重 V1：研究结论",
        "",
        f"最终状态：`{result['decision']}`",
        "",
        "本轮保持14日动量4入6出的成员与方向不变，只在成员变化时按当前动量排名分配固定权重：每侧20%、15%、10%、5%。它不是独立Alpha，不分配新H编号。",
        "",
        "## 核心结果",
        "",
        f"- 等权基准：PF {float(baseline['profit_factor'] or 0.0):.4f}，总收益 {float(baseline['total_return'] or 0.0):.2%}，最大回撤 {float(baseline['maximum_drawdown'] or 0.0):.2%}；",
        f"- 排名强度权重：PF {float(primary['profit_factor'] or 0.0):.4f}，总收益 {float(primary['total_return'] or 0.0):.2%}，最大回撤 {float(primary['maximum_drawdown'] or 0.0):.2%}；",
        f"- 压力成本：PF {float(stress['profit_factor'] or 0.0):.4f}，总收益 {float(stress['total_return'] or 0.0):.2%}；",
        f"- 平均换手 {float(primary['mean_turnover'] or 0.0):.4f}，实际资金费净贡献 {float(primary['funding_return'] or 0.0):.4%}。",
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
                else "失败门禁：" + "、".join(failed) + "。永久归档，禁止修改权重向量、刷新规则、成员规则、币种、成本或持有期营救。"
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
    entries, mappings, simple_score, log_score = shared._build_signal_inputs(panels, symbols)
    if len(entries) < 500:
        raise ValueError(f"insufficient daily signals: {len(entries)}")

    unique_sector = {symbol: symbol for symbol in symbols}
    baseline = sector_capped_hysteresis_weights(
        mappings,
        symbols,
        unique_sector,
        top_n=4,
        exit_rank=6,
        max_per_sector=4,
    )
    primary_weights = [float(value) for value in protocol["primary_variant"]["side_weights"]]
    primary = rank_conviction_weight_path(
        baseline,
        mappings,
        symbols,
        side_weights=primary_weights,
        refresh_only_on_membership_change=True,
    )
    log_mappings = _score_mappings(entries, log_score, symbols)
    log_primary = rank_conviction_weight_path(
        baseline,
        log_mappings,
        symbols,
        side_weights=primary_weights,
        refresh_only_on_membership_change=True,
    )

    variants = {"primary_20_15_10_05": primary}
    for declaration in protocol["parameter_neighborhood"]:
        config_id = str(declaration["config_id"])
        if bool(declaration["is_primary"]):
            continue
        variants[config_id] = rank_conviction_weight_path(
            baseline,
            mappings,
            symbols,
            side_weights=[float(value) for value in declaration["side_weights"]],
            refresh_only_on_membership_change=True,
        )

    funding, funding_coverage = shared._load_funding(symbols)
    funding_rates = shared._funding_rate_matrix(entries, symbols, funding)
    delayed_entries = [entry + pd.Timedelta(minutes=15) for entry in entries]
    delayed_funding_rates = shared._funding_rate_matrix(delayed_entries, symbols, funding)
    h4_open = panels.h4_open.loc[:, symbols]
    delayed_open = panels.m15_open.loc[:, symbols]

    base_cost = float(protocol["costs"]["one_way_baseline"])
    stress_cost = float(protocol["costs"]["one_way_stress"])
    baseline_frame = shared._simulate(
        entries,
        baseline,
        h4_open,
        funding_rates,
        one_way_cost=base_cost,
        adverse_funding_multiplier=1.0,
    )
    primary_frame = shared._simulate(
        entries,
        primary,
        h4_open,
        funding_rates,
        one_way_cost=base_cost,
        adverse_funding_multiplier=1.0,
    )
    stress_frame = shared._simulate(
        entries,
        primary,
        h4_open,
        funding_rates,
        one_way_cost=stress_cost,
        adverse_funding_multiplier=2.0,
    )
    baseline_metrics = shared._metrics(baseline_frame)
    primary_metrics = shared._metrics(primary_frame)
    stress_metrics = shared._metrics(stress_frame)
    segments = shared._segment_metrics(primary_frame)

    representation_agreement = float(np.mean(np.all(np.isclose(primary, log_primary), axis=1)))
    active_deviation_fraction = float(np.mean(np.any(np.abs(primary - baseline) > 1e-6, axis=1)))
    maximum_observed_weight = float(np.abs(primary).max())
    turnover_increase = float(primary_metrics["mean_turnover"] or 0.0) / float(
        baseline_metrics["mean_turnover"] or 1.0
    ) - 1.0
    structural_thresholds = protocol["structural_gates"]
    structural_checks = {
        "representation_agreement_at_least_95pct": representation_agreement
        >= float(structural_thresholds["minimum_representation_agreement"]),
        "conviction_weighting_is_materially_active": active_deviation_fraction
        >= float(structural_thresholds["minimum_active_weight_deviation_fraction"]),
        "maximum_symbol_weight_within_cap": maximum_observed_weight
        <= float(structural_thresholds["maximum_absolute_symbol_weight"]) + 1e-12,
        "turnover_increase_within_limit": turnover_increase
        <= float(structural_thresholds["maximum_turnover_increase_vs_baseline"]),
        "memberships_unchanged": bool(np.array_equal(np.sign(primary), np.sign(baseline))),
        "all_targets_market_neutral": bool(np.allclose(primary.sum(axis=1), 0.0)),
        "all_targets_unit_gross": bool(np.allclose(np.abs(primary).sum(axis=1), 1.0)),
    }
    structural = {
        "representation_agreement": representation_agreement,
        "active_deviation_fraction": active_deviation_fraction,
        "maximum_observed_absolute_weight": maximum_observed_weight,
        "turnover_increase_vs_baseline": turnover_increase,
        "checks": structural_checks,
        "passed": bool(all(structural_checks.values())),
    }

    positive_segments = sum(float(item.get("mean") or 0.0) > 0.0 for item in segments.values())
    historical_thresholds = protocol["historical_gates"]
    historical_checks = {
        "base_profit_factor_at_least_minimum": float(primary_metrics["profit_factor"] or 0.0)
        >= float(historical_thresholds["minimum_base_profit_factor"]),
        "stress_profit_factor_at_least_one": float(stress_metrics["profit_factor"] or 0.0)
        >= float(historical_thresholds["minimum_stress_profit_factor"]),
        "base_pf_loss_vs_baseline_within_limit": float(primary_metrics["profit_factor"] or 0.0)
        >= float(baseline_metrics["profit_factor"] or 0.0)
        - float(historical_thresholds["maximum_base_pf_loss_vs_baseline"]),
        "drawdown_increase_vs_baseline_within_limit": abs(float(primary_metrics["maximum_drawdown"] or 0.0))
        <= abs(float(baseline_metrics["maximum_drawdown"] or 0.0))
        + float(historical_thresholds["maximum_drawdown_increase_vs_baseline"]),
        "positive_in_at_least_two_segments": positive_segments
        >= int(historical_thresholds["minimum_positive_chronological_segments"]),
    }
    historical = {
        "positive_segments": positive_segments,
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
    neighborhood_frame, neighborhood = shared._parameter_neighborhood(
        entries, variants, h4_open, funding_rates, protocol
    )
    regimes = shared._regime_labels(panels, entries, symbols)
    portfolio_frame, portfolio = shared._portfolio_increment(
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
    decision = (
        "HISTORICALLY_SUPPORTED_RESEARCH_SHADOW_ONLY"
        if passed
        else "REJECT_AND_ARCHIVE_NO_RESCUE"
    )
    result: dict[str, Any] = {
        "schema": "momentum_rank_conviction_evaluation_v1",
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
        "decision": decision,
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
    hashes = {
        path.name: shared._sha256(path)
        for path in sorted(output_dir.iterdir())
        if path.is_file() and path.name != "SHA256SUMS.json"
    }
    shared._write_json(output_dir / "SHA256SUMS.json", hashes)

    if not passed:
        archive_dir = _archive_failure(output_dir, result)
        result["failure_archive"] = str(archive_dir)
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
