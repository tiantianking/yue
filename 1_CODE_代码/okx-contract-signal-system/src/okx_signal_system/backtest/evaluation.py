from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EvaluationThresholds:
    """
    评估阈值配置

    核心原则：盈亏比(Profit Factor)为主，胜率为辅助参考
    - 盈亏比 >= 1.05 是基础门槛
    - 盈亏比 >= 1.20 是优秀门槛
    - 胜率 >= 35% 是最低要求（配合高盈亏比）
    """
    min_valid_profit_factor: float = 1.05
    min_valid_payoff_ratio: float = 1.20
    max_valid_drawdown: float = 0.20
    max_valid_drawdown_train_multiple: float = 1.25
    min_symbol_valid_trades: int = 15
    min_portfolio_valid_trades: int = 80
    min_profitable_symbol_ratio: float = 0.55
    max_hit_27pct_symbol_ratio: float = 0.20
    max_gt5x_trade_pct: float = 0.15
    max_pnl_share_from_gt5x: float = 0.30
    # 新增：胜率最低门槛（配合高盈亏比时适当放宽）
    min_win_rate: float = 0.35
    # 新增：高盈亏比时的胜率宽容度（盈亏比>1.5时，胜率可降至30%）
    high_pf_win_rate_threshold: float = 1.50


def evaluate_symbol(train: dict, valid: dict, thresholds: EvaluationThresholds = EvaluationThresholds()) -> dict[str, str | bool]:
    reasons: list[str] = []

    # 1. 收益率检查（基础）
    if train["total_return"] <= 0:
        reasons.append("train_return_not_positive")
    if valid["total_return"] <= 0:
        reasons.append("valid_return_not_positive")

    # 2. 盈亏比检查（核心指标，权重最高）
    if valid["profit_factor"] < thresholds.min_valid_profit_factor:
        reasons.append("valid_profit_factor_below_1_05")

    # 3. 盈亏比 >= 1.5 时，胜率要求适当放宽
    win_rate = valid.get("win_rate", 0)
    pf = valid["profit_factor"]
    if pf >= thresholds.high_pf_win_rate_threshold:
        # 高盈亏比时，胜率可接受30%以上
        if win_rate < 0.30:
            reasons.append("valid_win_rate_too_low_even_with_high_pf")
    elif win_rate < thresholds.min_win_rate:
        reasons.append("valid_win_rate_below_35pct")

    # 4. 盈亏比检查（辅助参考）
    if valid["payoff_ratio"] < thresholds.min_valid_payoff_ratio:
        reasons.append("valid_payoff_below_1_20")

    # 5. 回撤检查
    if valid["max_drawdown"] > thresholds.max_valid_drawdown:
        reasons.append("valid_drawdown_above_20pct")
    if train["max_drawdown"] > 0 and valid["max_drawdown"] > train["max_drawdown"] * thresholds.max_valid_drawdown_train_multiple:
        reasons.append("valid_drawdown_above_train_multiple")

    # 6. 交易数检查
    if valid["total_trades"] < thresholds.min_symbol_valid_trades:
        reasons.append("valid_trade_count_below_15")

    # 7. 极端亏损检查
    if valid["hit_27pct_stop"]:
        reasons.append("hit_27pct_stop")

    # 8. 杠杆风险检查
    if valid.get("near_liq_trades", 0) > 0:
        reasons.append("near_liq_trades_present")
    if valid.get("gt5x_trade_pct", 0.0) > thresholds.max_gt5x_trade_pct:
        reasons.append("gt5x_trade_dependency")
    if valid["pnl_share_from_gt5x"] > thresholds.max_pnl_share_from_gt5x:
        reasons.append("gt5x_profit_dependency")

    return {
        "pass_fail": "failed" if reasons else "passed",
        "reasons": ",".join(reasons),
        "passed": not reasons,
        "pf": pf,
        "win_rate": win_rate,
        "payoff_ratio": valid["payoff_ratio"],
        "total_return": valid["total_return"],
    }


def evaluate_portfolio(
    train: dict,
    valid: dict,
    *,
    profitable_symbol_ratio: float,
    hit_27pct_symbol_ratio: float,
    thresholds: EvaluationThresholds = EvaluationThresholds(),
) -> dict[str, str | bool]:
    reasons: list[str] = []

    # 1. 收益率检查
    if train["total_return"] <= 0:
        reasons.append("portfolio_train_return_not_positive")
    if valid["total_return"] <= 0:
        reasons.append("portfolio_valid_return_not_positive")

    # 2. 盈亏比检查（核心）
    if valid["profit_factor"] < thresholds.min_valid_profit_factor:
        reasons.append("portfolio_valid_profit_factor_below_1_05")

    # 3. 盈亏比 >= 1.5 时胜率放宽
    win_rate = valid.get("win_rate", 0)
    pf = valid["profit_factor"]
    if pf >= thresholds.high_pf_win_rate_threshold:
        if win_rate < 0.30:
            reasons.append("portfolio_win_rate_too_low_even_with_high_pf")
    elif win_rate < thresholds.min_win_rate:
        reasons.append("portfolio_win_rate_below_35pct")

    # 4. 盈亏比（辅助）
    if valid["payoff_ratio"] < thresholds.min_valid_payoff_ratio:
        reasons.append("portfolio_valid_payoff_below_1_20")

    # 5. 回撤
    if valid["max_drawdown"] > thresholds.max_valid_drawdown:
        reasons.append("portfolio_valid_drawdown_above_20pct")
    if train["max_drawdown"] > 0 and valid["max_drawdown"] > train["max_drawdown"] * thresholds.max_valid_drawdown_train_multiple:
        reasons.append("portfolio_valid_drawdown_above_train_multiple")

    # 6. 交易数
    if valid["total_trades"] < thresholds.min_portfolio_valid_trades:
        reasons.append("portfolio_valid_trade_count_below_80")

    # 7. 盈利币种比例
    if profitable_symbol_ratio < thresholds.min_profitable_symbol_ratio:
        reasons.append("profitable_symbol_ratio_below_55pct")
    if hit_27pct_symbol_ratio > thresholds.max_hit_27pct_symbol_ratio:
        reasons.append("hit_27pct_symbol_ratio_above_20pct")

    # 8. 杠杆风险
    if valid.get("near_liq_trades", 0) > 0:
        reasons.append("portfolio_near_liq_trades_present")
    if valid.get("gt5x_trade_pct", 0.0) > thresholds.max_gt5x_trade_pct:
        reasons.append("portfolio_gt5x_trade_dependency")
    if valid["pnl_share_from_gt5x"] > thresholds.max_pnl_share_from_gt5x:
        reasons.append("portfolio_gt5x_profit_dependency")

    return {
        "pass_fail": "failed" if reasons else "passed",
        "reasons": ",".join(reasons),
        "passed": not reasons,
        "pf": pf,
        "win_rate": win_rate,
        "payoff_ratio": valid["payoff_ratio"],
        "total_return": valid["total_return"],
    }
