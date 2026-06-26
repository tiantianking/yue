from __future__ import annotations

"""Frozen causal funding-carry tilt audit for the existing momentum shadow."""

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
from okx_signal_system.research.funding_carry_tilt import (
    ambiguous_side,
    carry_benefit,
    causal_recent_funding_mean,
    target_turnover,
)
from okx_signal_system.research.rank_conviction_weighting import rank_conviction_weight_path
from okx_signal_system.research.sector_balanced_momentum import sector_capped_hysteresis_weights

PROTOCOL_PATH = PROJECT_ROOT / "config" / "research_protocols" / "momentum_funding_carry_tilt_v1.json"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "outputs" / "research" / "momentum_funding_carry_tilt_v1"


def _protocol_symbols(protocol: dict[str, Any]) -> list[str]:
    if protocol.get("status") != "LOCKED_BEFORE_PNL":
        raise ValueError("protocol must be locked before outcomes are opened")
    symbols = [str(value) for value in protocol.get("universe", {}).get("symbols", [])]
    if len(symbols) != 18 or len(set(symbols)) != 18:
        raise ValueError("frozen universe must contain 18 unique mature symbols")
    return symbols


def _carry_score_rows(
    entries: list[pd.Timestamp],
    memberships: np.ndarray,
    symbols: list[str],
    funding: dict[str, pd.DataFrame],
) -> tuple[list[dict[str, float]], dict[str, Any]]:
    rows: list[dict[str, float]] = []
    refresh_flags: list[bool] = []
    ambiguous_flags: list[bool] = []
    dispersion_flags: list[bool] = []
    complete_positions = 0
    total_positions = 0
    previous_membership: np.ndarray | None = None

    for row_index, entry in enumerate(entries):
        membership = np.sign(memberships[row_index])
        refresh = previous_membership is None or not np.array_equal(membership, previous_membership)
        refresh_flags.append(refresh)
        mapping = {symbol: 0.0 for symbol in symbols}
        held_scores: dict[int, float] = {}
        for column, symbol in enumerate(symbols):
            sign = float(membership[column])
            if sign == 0.0:
                continue
            total_positions += 1
            frame = funding[symbol]
            mean_rate = causal_recent_funding_mean(
                frame["funding_time"],
                frame["funding_rate"],
                entry,
                settlements=3,
            )
            if mean_rate is None:
                mapping[symbol] = float("nan")
                continue
            complete_positions += 1
            benefit = carry_benefit(sign, mean_rate)
            mapping[symbol] = benefit
            held_scores[column] = benefit
        rows.append(mapping)

        if refresh:
            long_scores = [held_scores[index] for index in np.flatnonzero(membership > 0.0) if index in held_scores]
            short_scores = [held_scores[index] for index in np.flatnonzero(membership < 0.0) if index in held_scores]
            complete = len(long_scores) == 4 and len(short_scores) == 4
            ambiguous_flags.append(
                True if not complete else ambiguous_side(long_scores) or ambiguous_side(short_scores)
            )
            dispersion_flags.append(
                False
                if not complete
                else (max(long_scores) - min(long_scores) > 1e-12)
                and (max(short_scores) - min(short_scores) > 1e-12)
            )
        previous_membership = membership.copy()

    refresh_count = sum(refresh_flags)
    return rows, {
        "complete_score_fraction": complete_positions / total_positions if total_positions else 0.0,
        "refresh_count": refresh_count,
        "ambiguous_refresh_fraction": float(np.mean(ambiguous_flags)) if ambiguous_flags else 1.0,
        "nonzero_dispersion_fraction": float(np.mean(dispersion_flags)) if dispersion_flags else 0.0,
    }


def _markdown(result: dict[str, Any]) -> str:
    decision = result["decision"]
    lines = [
        "# 14日动量资金费持有成本倾斜 V1：研究结论",
        "",
        f"最终状态：`{decision}`",
        "",
        "本轮保持14日动量4入6出的成员和方向不变，只使用入场前已经结算的最近3次OKX资金费，对每侧权重做轻度持有成本倾斜。",
        "",
        "## 收益前结构审计",
        "",
    ]
    pre = result["pre_pnl"]
    lines.extend(
        [
            f"- 完整资金费得分比例：{float(pre['complete_score_fraction']):.2%}；",
            f"- 权重刷新次数：{int(pre['refresh_count'])}；",
            f"- 刷新时并列歧义比例：{float(pre['ambiguous_refresh_fraction']):.2%}；",
            f"- 两侧均有非零资金费分散的比例：{float(pre['nonzero_dispersion_fraction']):.2%}；",
            f"- 相对等权目标换手增幅：{float(pre.get('target_turnover_increase_vs_baseline', 0.0)):.2%}。",
            "",
        ]
    )
    if not result["pnl_opened"]:
        lines.extend(
            [
                "收益前门禁失败，因此没有打开未来收益、PF、胜率或回撤。该版本永久归档，禁止修改资金费窗口、并列规则或权重向量营救。",
                "",
                "生产系统影响：`NONE`  ",
                "自动下单影响：`NONE`",
                "",
            ]
        )
        return "\n".join(lines)

    baseline = result["performance"]["baseline_base"]
    primary = result["performance"]["primary_base"]
    stress = result["performance"]["primary_stress"]
    lines.extend(
        [
            "## 核心结果",
            "",
            f"- 等权基准PF {float(baseline['profit_factor'] or 0.0):.4f}，总收益 {float(baseline['total_return'] or 0.0):.2%}；",
            f"- 资金费倾斜PF {float(primary['profit_factor'] or 0.0):.4f}，总收益 {float(primary['total_return'] or 0.0):.2%}，最大回撤 {float(primary['maximum_drawdown'] or 0.0):.2%}；",
            f"- 压力成本PF {float(stress['profit_factor'] or 0.0):.4f}，总收益 {float(stress['total_return'] or 0.0):.2%}。",
            "",
            "## 门禁",
            "",
        ]
    )
    for name, group in result["gate_groups"].items():
        lines.append(f"- {name}: {'通过' if group['passed'] else '失败'}")
    lines.extend(
        [
            "",
            "只允许按冻结结论处理；不得修改资金费窗口、权重向量、并列规则、成本或成员规则营救。",
            "",
            "生产系统影响：`NONE`  ",
            "自动下单影响：`NONE`",
            "",
        ]
    )
    return "\n".join(lines)


def _archive(output_dir: Path, result: dict[str, Any]) -> Path:
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
            "failed_stage": "pre_pnl" if not result["pnl_opened"] else "historical_robustness_and_incremental_value",
            "pnl_opened": result["pnl_opened"],
            "independent_alpha_claim": False,
            "failed_gate_groups": [
                name for name, group in result.get("gate_groups", {}).items() if not group["passed"]
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
    unique_sector = {symbol: symbol for symbol in symbols}
    baseline = sector_capped_hysteresis_weights(
        momentum_mappings,
        symbols,
        unique_sector,
        top_n=4,
        exit_rank=6,
        max_per_sector=4,
    )
    funding, funding_coverage = shared._load_funding(symbols)
    carry_rows, pre_pnl = _carry_score_rows(entries, baseline, symbols, funding)
    primary = rank_conviction_weight_path(
        baseline,
        carry_rows,
        symbols,
        side_weights=protocol["primary_variant"]["side_weights_by_carry_benefit"],
        refresh_only_on_membership_change=True,
    )
    baseline_turnover = target_turnover(baseline)
    primary_turnover = target_turnover(primary)
    pre_pnl["target_turnover_increase_vs_baseline"] = primary_turnover / baseline_turnover - 1.0
    gates = protocol["pre_pnl_gates"]
    pre_checks = {
        "complete_score_fraction": pre_pnl["complete_score_fraction"] >= float(gates["minimum_complete_score_fraction"]),
        "ambiguous_refresh_fraction": pre_pnl["ambiguous_refresh_fraction"] <= float(gates["maximum_ambiguous_refresh_fraction"]),
        "nonzero_dispersion_fraction": pre_pnl["nonzero_dispersion_fraction"] >= float(gates["minimum_nonzero_dispersion_fraction"]),
        "target_turnover_increase": pre_pnl["target_turnover_increase_vs_baseline"] <= float(gates["maximum_target_turnover_increase_vs_baseline"]),
    }
    pre_pnl["checks"] = pre_checks
    pre_pnl["passed"] = bool(all(pre_checks.values()))

    output_dir.mkdir(parents=True, exist_ok=True)
    if not pre_pnl["passed"]:
        result: dict[str, Any] = {
            "schema": "momentum_funding_carry_tilt_evaluation_v1",
            "protocol_id": protocol["protocol_id"],
            "protocol_locked_before_pnl": True,
            "pnl_opened": False,
            "signal_count": len(entries),
            "funding_coverage": funding_coverage,
            "pre_pnl": pre_pnl,
            "decision": "REJECT_BEFORE_PNL_NO_RESCUE",
            "production_effect": "NONE",
            "automatic_ordering": False,
        }
        shared._write_json(output_dir / "result.json", result)
        (output_dir / "RESULTS_CN.md").write_text(_markdown(result), encoding="utf-8")
        shutil.copy2(PROTOCOL_PATH, output_dir / PROTOCOL_PATH.name)
        result["failure_archive"] = str(_archive(output_dir, result))
        shared._write_json(output_dir / "result.json", result)
        return result

    variants = {"primary_15_13_12_10": primary}
    for declaration in protocol["parameter_neighborhood"]:
        if declaration["is_primary"]:
            continue
        variants[str(declaration["config_id"])] = rank_conviction_weight_path(
            baseline,
            carry_rows,
            symbols,
            side_weights=declaration["side_weights"],
            refresh_only_on_membership_change=True,
        )

    funding_rates = shared._funding_rate_matrix(entries, symbols, funding)
    delayed_entries = [entry + pd.Timedelta(minutes=15) for entry in entries]
    delayed_funding_rates = shared._funding_rate_matrix(delayed_entries, symbols, funding)
    h4_open = panels.h4_open.loc[:, symbols]
    delayed_open = panels.m15_open.loc[:, symbols]
    base_cost = float(protocol["costs"]["one_way_baseline"])
    stress_cost = float(protocol["costs"]["one_way_stress"])
    baseline_frame = shared._simulate(entries, baseline, h4_open, funding_rates, one_way_cost=base_cost, adverse_funding_multiplier=1.0)
    primary_frame = shared._simulate(entries, primary, h4_open, funding_rates, one_way_cost=base_cost, adverse_funding_multiplier=1.0)
    stress_frame = shared._simulate(entries, primary, h4_open, funding_rates, one_way_cost=stress_cost, adverse_funding_multiplier=2.0)
    baseline_metrics = shared._metrics(baseline_frame)
    primary_metrics = shared._metrics(primary_frame)
    stress_metrics = shared._metrics(stress_frame)
    segments = shared._segment_metrics(primary_frame)
    hist = protocol["historical_gates"]
    positive_segments = sum(float(item.get("mean") or 0.0) > 0.0 for item in segments.values())
    historical_checks = {
        "base_profit_factor": float(primary_metrics["profit_factor"] or 0.0) >= float(hist["minimum_base_profit_factor"]),
        "stress_profit_factor": float(stress_metrics["profit_factor"] or 0.0) >= float(hist["minimum_stress_profit_factor"]),
        "pf_loss_vs_baseline": float(primary_metrics["profit_factor"] or 0.0) >= float(baseline_metrics["profit_factor"] or 0.0) - float(hist["maximum_base_pf_loss_vs_baseline"]),
        "drawdown_vs_baseline": abs(float(primary_metrics["maximum_drawdown"] or 0.0)) <= abs(float(baseline_metrics["maximum_drawdown"] or 0.0)) + float(hist["maximum_drawdown_increase_vs_baseline"]),
        "positive_segments": positive_segments >= int(hist["minimum_positive_chronological_segments"]),
    }
    falsification_frame, falsification, observed_frame, _ = shared._falsification_rows(
        entries, primary, h4_open, delayed_open, funding_rates, delayed_funding_rates, protocol
    )
    neighborhood_frame, neighborhood = shared._parameter_neighborhood(entries, variants, h4_open, funding_rates, protocol)
    regimes = shared._regime_labels(panels, entries, symbols)
    portfolio_frame, portfolio = shared._portfolio_increment(entries, baseline, primary, h4_open, funding_rates, regimes, protocol)
    gate_groups = {
        "pre_pnl": {"passed": True, "checks": pre_checks},
        "historical_cost_and_segments": {"passed": bool(all(historical_checks.values())), "checks": historical_checks},
        "falsification": {"passed": falsification["passed"], "checks": falsification["checks"]},
        "parameter_neighborhood": {"passed": neighborhood["passed"], "checks": neighborhood["checks"]},
        "portfolio_increment": {"passed": portfolio["passed"], "checks": {**portfolio["no_deterioration_checks"], "at_least_one_material_improvement": any(portfolio["improvement_checks"].values())}},
    }
    passed = bool(all(group["passed"] for group in gate_groups.values()))
    result = {
        "schema": "momentum_funding_carry_tilt_evaluation_v1",
        "protocol_id": protocol["protocol_id"],
        "protocol_locked_before_pnl": True,
        "pnl_opened": True,
        "signal_count": len(entries),
        "funding_coverage": funding_coverage,
        "pre_pnl": pre_pnl,
        "performance": {"baseline_base": baseline_metrics, "primary_base": primary_metrics, "primary_stress": stress_metrics},
        "historical": {"segments": segments, "positive_segments": positive_segments},
        "falsification": falsification,
        "parameter_neighborhood": neighborhood,
        "portfolio_increment": portfolio,
        "gate_groups": gate_groups,
        "decision": "HISTORICALLY_SUPPORTED_RESEARCH_SHADOW_ONLY" if passed else "REJECT_AND_ARCHIVE_NO_RESCUE",
        "production_effect": "NONE",
        "automatic_ordering": False,
    }
    falsification_frame.to_csv(output_dir / "falsification_trials.csv", index=False)
    neighborhood_frame.to_csv(output_dir / "parameter_neighborhood.csv", index=False)
    portfolio_frame.to_csv(output_dir / "portfolio_increment.csv", index=False)
    observed_frame.to_csv(output_dir / "primary_interval_returns.csv", index=False)
    shared._write_json(output_dir / "result.json", result)
    (output_dir / "RESULTS_CN.md").write_text(_markdown(result), encoding="utf-8")
    shutil.copy2(PROTOCOL_PATH, output_dir / PROTOCOL_PATH.name)
    if not passed:
        result["failure_archive"] = str(_archive(output_dir, result))
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
        print(json.dumps({"protocol_id": result["protocol_id"], "decision": result["decision"], "pnl_opened": result["pnl_opened"], "failure_archive": result.get("failure_archive")}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
