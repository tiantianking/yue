from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EvaluationThresholds:
    min_valid_profit_factor: float = 1.05
    min_valid_payoff_ratio: float = 1.25
    max_valid_drawdown: float = 0.15
    min_symbol_valid_trades: int = 15
    max_pnl_share_from_gt5x: float = 0.25


def evaluate_symbol(train: dict, valid: dict, thresholds: EvaluationThresholds = EvaluationThresholds()) -> dict[str, str | bool]:
    reasons: list[str] = []
    if train["total_return"] > 0 and valid["total_return"] < 0:
        reasons.append("train_positive_valid_negative")
    if valid["profit_factor"] < thresholds.min_valid_profit_factor:
        reasons.append("valid_profit_factor_below_1_05")
    if valid["payoff_ratio"] < thresholds.min_valid_payoff_ratio:
        reasons.append("valid_payoff_below_1_25")
    if valid["max_drawdown"] > thresholds.max_valid_drawdown:
        reasons.append("valid_drawdown_above_15pct")
    if valid["total_trades"] < thresholds.min_symbol_valid_trades:
        reasons.append("valid_trade_count_below_15")
    if valid["hit_27pct_stop"]:
        reasons.append("hit_27pct_stop")
    if valid["pnl_share_from_gt5x"] > thresholds.max_pnl_share_from_gt5x:
        reasons.append("gt5x_profit_dependency")
    return {"pass_fail": "failed" if reasons else "passed", "reasons": ",".join(reasons), "passed": not reasons}
