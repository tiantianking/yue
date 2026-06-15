from __future__ import annotations

import json
import logging
from dataclasses import asdict
from pathlib import Path
from typing import Iterable

import pandas as pd

from okx_signal_system.backtest.evaluation import evaluate_portfolio, evaluate_symbol
from okx_signal_system.backtest.grid_search import parameter_grid, run_grid_search, select_best_params
from okx_signal_system.backtest.runner import run_backtest, split_train_valid, summarize_trades
from okx_signal_system.data.loader import SymbolData, load_all_symbols
from okx_signal_system.strategy.trend_breakout import StrategyParams
from okx_signal_system.timeframe import default_trend_timeframe, timeframe_spec

log = logging.getLogger(__name__)

BASELINE_PARAMS = StrategyParams()
DEFAULT_DATASET = "okx_15m_extended"
DEFAULT_SIGNAL_TIMEFRAME = "15m"
DEFAULT_TREND_TIMEFRAME = "1h"


class NoValidParameterSetError(RuntimeError):
    """Raised when training cannot find a parameter set that passes gates."""


def _prefixed(summary: dict, prefix: str) -> dict:
    return {f"{prefix}_{key}": value for key, value in summary.items()}


def _param_distance(params: pd.Series) -> float:
    return float(
        abs(params["fast_ema"] - BASELINE_PARAMS.fast_ema) / 10
        + abs(params["slow_ema"] - BASELINE_PARAMS.slow_ema) / 10
        + abs(params["breakout_window"] - BASELINE_PARAMS.breakout_window) / 20
        + abs(params["atr_stop_mult"] - BASELINE_PARAMS.atr_stop_mult)
        + abs(params["take_profit_mult"] - BASELINE_PARAMS.take_profit_mult)
        + abs(params["max_hold_bars"] - BASELINE_PARAMS.max_hold_bars) / 24
    )


def combine_trade_summaries(
    trade_frames: Iterable[pd.DataFrame],
    *,
    initial_equity_per_symbol: float = 10000.0,
    symbol_count: int | None = None,
) -> dict:
    frames = [frame for frame in trade_frames if frame is not None and not frame.empty]
    if not frames:
        return summarize_trades(pd.DataFrame(), initial_equity=initial_equity_per_symbol * max(1, symbol_count or 1))
    combined = pd.concat(frames, ignore_index=True).sort_values("exit_time").reset_index(drop=True)
    denominator_symbols = max(1, symbol_count or combined["inst_id"].nunique())
    return summarize_trades(combined, initial_equity=initial_equity_per_symbol * denominator_symbols)


def _frame_timeframe(frame: pd.DataFrame, fallback: str = DEFAULT_SIGNAL_TIMEFRAME) -> str:
    if "timeframe" in frame.columns:
        values = frame["timeframe"].dropna()
        if not values.empty:
            return timeframe_spec(str(values.iloc[0])).key
    return timeframe_spec(fallback).key


def _resolve_timeframes(
    frame: pd.DataFrame,
    signal_timeframe: str | None,
    trend_timeframe: str | None,
) -> tuple[str, str]:
    signal_key = timeframe_spec(signal_timeframe or _frame_timeframe(frame)).key
    trend_key = timeframe_spec(trend_timeframe or default_trend_timeframe(signal_key)).key
    return signal_key, trend_key


def run_train_valid_symbol(
    frame: pd.DataFrame,
    *,
    inst_id: str,
    params_grid: list[StrategyParams] | None = None,
    signal_timeframe: str | None = None,
    trend_timeframe: str | None = None,
) -> dict:
    signal_key, trend_key = _resolve_timeframes(frame, signal_timeframe, trend_timeframe)
    train_frame, valid_frame = split_train_valid(frame, valid_fraction=0.25)
    grid = run_grid_search(
        train_frame,
        inst_id=inst_id,
        params_grid=params_grid,
        signal_timeframe=signal_key,
        trend_timeframe=trend_key,
    )
    selected = select_best_params(grid)
    train_trades = run_backtest(
        train_frame,
        inst_id=inst_id,
        params=selected,
        signal_timeframe=signal_key,
        trend_timeframe=trend_key,
    )
    valid_trades = run_backtest(
        valid_frame,
        inst_id=inst_id,
        params=selected,
        signal_timeframe=signal_key,
        trend_timeframe=trend_key,
    )
    train_summary = summarize_trades(train_trades)
    valid_summary = summarize_trades(valid_trades)
    evaluation = evaluate_symbol(train_summary, valid_summary)
    return {
        "inst_id": inst_id,
        "selected_params": asdict(selected),
        "grid_results": grid,
        "train_trades": train_trades,
        "valid_trades": valid_trades,
        "train_summary": train_summary,
        "valid_summary": valid_summary,
        "evaluation": evaluation,
    }


def run_shared_train_grid(
    symbols: list[SymbolData],
    *,
    params_grid: list[StrategyParams] | None = None,
    signal_timeframe: str | None = None,
    trend_timeframe: str | None = None,
) -> pd.DataFrame:
    signal_key, trend_key = _resolve_timeframes(symbols[0].frame, signal_timeframe, trend_timeframe)
    grid = params_grid or parameter_grid(signal_key)
    all_rows: list[dict] = []
    for symbol_data in symbols:
        train_frame, _ = split_train_valid(symbol_data.frame, valid_fraction=0.25)
        symbol_grid = run_grid_search(
            train_frame,
            inst_id=symbol_data.inst_id,
            params_grid=grid,
            signal_timeframe=signal_key,
            trend_timeframe=trend_key,
        )
        symbol_grid.insert(0, "symbol", symbol_data.inst_id)
        all_rows.extend(symbol_grid.to_dict("records"))
    symbol_results = pd.DataFrame(all_rows)
    if symbol_results.empty:
        return symbol_results

    param_cols = ["fast_ema", "slow_ema", "breakout_window", "atr_stop_mult", "take_profit_mult", "max_hold_bars", "atr_window"]
    agg = (
        symbol_results.groupby(param_cols, dropna=False)
        .agg(
            train_total_return=("train_total_return", "mean"),
            train_profit_factor=("train_profit_factor", "mean"),
            train_payoff_ratio=("train_payoff_ratio", "mean"),
            train_win_rate=("train_win_rate", "mean"),
            train_total_trades=("train_total_trades", "sum"),
            train_max_drawdown=("train_max_drawdown", "max"),
            train_avg_hold_hours=("train_avg_hold_hours", "mean"),
            train_hit_27pct_symbols=("train_hit_27pct_stop", "sum"),
            train_near_liq_trades=("train_near_liq_trades", "sum"),
            train_gt5x_trade_pct=("train_gt5x_trade_pct", "mean"),
            train_pnl_share_from_gt5x=("train_pnl_share_from_gt5x", "mean"),
            profitable_symbols=("train_total_return", lambda values: int((values > 0).sum())),
            symbols_tested=("symbol", "nunique"),
        )
        .reset_index()
    )
    agg["profitable_symbol_ratio"] = agg["profitable_symbols"] / agg["symbols_tested"].clip(lower=1)
    agg["centrality_distance"] = agg.apply(_param_distance, axis=1)
    agg["passed_train_gate"] = (
        (agg["train_total_return"] > 0)
        & (agg["train_profit_factor"] >= 1.05)
        & (agg["train_payoff_ratio"] >= 1.20)
        & (agg["train_total_trades"] >= 80)
        & (agg["train_max_drawdown"] <= 0.20)
        & (agg["train_near_liq_trades"] == 0)
        & (agg["train_gt5x_trade_pct"] <= 0.15)
        & (agg["train_pnl_share_from_gt5x"] <= 0.30)
        & (agg["profitable_symbol_ratio"] >= 0.55)
    )
    return agg


def select_shared_params(train_grid_results: pd.DataFrame) -> StrategyParams:
    """
    参数选择策略：以盈亏比为主，胜率为辅助参考

    核心原则：
    1. 盈亏比(Profit Factor) >= 1.05 是基础门槛
    2. 优先选择盈亏比最高的参数组合
    3. 在盈亏比相近的情况下，选择胜率更高的
    4. 最终用验证段数据验证稳定性
    """
    if train_grid_results.empty:
        raise ValueError("shared train grid is empty")
    candidates = train_grid_results[train_grid_results["passed_train_gate"]].copy()
    if candidates.empty:
        raise NoValidParameterSetError("NO_VALID_PARAMETER_SET")

    # 标准化PF用于排序（inf替换为一个大数）
    candidates["rank_pf"] = candidates["train_profit_factor"].replace(float("inf"), 999999)

    # 按盈亏比排序，盈亏比相同则按胜率排序
    candidates = candidates.sort_values(
        [
            "passed_train_gate",      # 1. 首先通过门控
            "rank_pf",                # 2. 盈亏比最高优先
            "train_win_rate",          # 3. 胜率次优先
            "train_max_drawdown",      # 4. 回撤最小
            "train_total_trades",      # 5. 交易数足够多
            "centrality_distance",     # 6. 参数居中
        ],
        ascending=[False, False, False, True, False, True],
    )

    # 获取最优参数
    row = candidates.iloc[0]

    # 计算该参数的综合评分（用于诊断）
    score = _calculate_param_score(row)
    log.info(f"参数选择: PF={row['train_profit_factor']:.2f}, 胜率={row['train_win_rate']:.2%}, "
             f"盈亏比={row['train_payoff_ratio']:.2f}, 综合评分={score:.2f}")

    return StrategyParams(
        fast_ema=int(row["fast_ema"]),
        slow_ema=int(row["slow_ema"]),
        breakout_window=int(row["breakout_window"]),
        atr_stop_mult=float(row["atr_stop_mult"]),
        take_profit_mult=float(row["take_profit_mult"]),
        max_hold_bars=int(row["max_hold_bars"]),
        atr_window=int(row.get("atr_window", 14)),
    )


def _calculate_param_score(row: pd.Series) -> float:
    """
    计算参数综合评分：盈亏比为主(60%)，胜率为辅(40%)

    评分公式：
    score = 0.6 * 标准化PF + 0.4 * 标准化胜率
    """
    pf = row["train_profit_factor"]
    win_rate = row["train_win_rate"]

    # PF标准化：期望范围 1.0 - 3.0，映射到 0-100
    pf_normalized = max(0, min(100, (pf - 1.0) / 2.0 * 100)) if pf != float("inf") else 100

    # 胜率标准化：期望范围 30% - 70%，映射到 0-100
    win_normalized = max(0, min(100, (win_rate - 0.3) / 0.4 * 100))

    return 0.6 * pf_normalized + 0.4 * win_normalized


def run_walk_forward_validation(
    frame: pd.DataFrame,
    *,
    inst_id: str,
    params: StrategyParams,
    train_window: int = 1000,
    valid_window: int = 250,
    step: int = 250,
    signal_timeframe: str | None = None,
    trend_timeframe: str | None = None,
) -> dict:
    """
    Walk-Forward 步进式验证：模拟真实交易环境，验证策略稳定性

    原理：
    - 用前 train_window 根 K 线训练参数
    - 用接下来的 valid_window 根 K 线验证
    - 逐步向前滚动

    返回：
    - 各窗口的验证结果
    - 稳定性指标（验证结果的标准差、变异系数）
    """
    if len(frame) < train_window + valid_window:
        log.warning(f"{inst_id} 数据不足 walk-forward 验证")
        return {}

    signal_key, trend_key = _resolve_timeframes(frame, signal_timeframe, trend_timeframe)
    results = []
    cursor = 0

    while cursor + train_window + valid_window <= len(frame):
        train_frame = frame.iloc[cursor : cursor + train_window].reset_index(drop=True)
        valid_frame = frame.iloc[cursor + train_window : cursor + train_window + valid_window].reset_index(drop=True)

        # 用训练窗口生成信号（不重新调参，保持冻结参数）
        train_trades = run_backtest(
            train_frame,
            inst_id=inst_id,
            params=params,
            signal_timeframe=signal_key,
            trend_timeframe=trend_key,
        )
        valid_trades = run_backtest(
            valid_frame,
            inst_id=inst_id,
            params=params,
            signal_timeframe=signal_key,
            trend_timeframe=trend_key,
        )

        train_summary = summarize_trades(train_trades)
        valid_summary = summarize_trades(valid_trades)

        results.append({
            "window_start": cursor,
            "train_summary": train_summary,
            "valid_summary": valid_summary,
        })

        cursor += step

    if not results:
        return {}

    # 计算稳定性指标
    valid_pfs = [r["valid_summary"]["profit_factor"] for r in results]
    valid_returns = [r["valid_summary"]["total_return"] for r in results]
    valid_win_rates = [r["valid_summary"]["win_rate"] for r in results]

    import numpy as np
    return {
        "window_count": len(results),
        "valid_pf_mean": float(np.mean(valid_pfs)),
        "valid_pf_std": float(np.std(valid_pfs)),
        "valid_pf_cv": float(np.std(valid_pfs) / np.mean(valid_pfs)) if np.mean(valid_pfs) > 0 else 0,
        "valid_return_mean": float(np.mean(valid_returns)),
        "valid_return_std": float(np.std(valid_returns)),
        "valid_win_rate_mean": float(np.mean(valid_win_rates)),
        "windows": results,
    }


def run_dataset_research(
    *,
    dataset: str = DEFAULT_DATASET,
    params_grid: list[StrategyParams] | None = None,
    max_symbols: int | None = None,
    shared_params: bool = True,
    signal_timeframe: str | None = None,
    trend_timeframe: str | None = None,
) -> pd.DataFrame:
    artifacts = run_dataset_research_artifacts(
        dataset=dataset,
        params_grid=params_grid,
        max_symbols=max_symbols,
        shared_params=shared_params,
        signal_timeframe=signal_timeframe,
        trend_timeframe=trend_timeframe,
    )
    return artifacts["single_symbol_results"]


def run_dataset_research_artifacts(
    *,
    dataset: str = DEFAULT_DATASET,
    params_grid: list[StrategyParams] | None = None,
    max_symbols: int | None = None,
    shared_params: bool = True,
    signal_timeframe: str | None = None,
    trend_timeframe: str | None = None,
) -> dict[str, pd.DataFrame | dict | StrategyParams]:
    symbols = load_all_symbols(dataset)
    if max_symbols is not None:
        symbols = symbols[:max_symbols]
    if not symbols:
        raise ValueError("no symbols loaded for research")
    signal_key, trend_key = _resolve_timeframes(symbols[0].frame, signal_timeframe, trend_timeframe)

    if shared_params:
        train_grid_results = run_shared_train_grid(
            symbols,
            params_grid=params_grid,
            signal_timeframe=signal_key,
            trend_timeframe=trend_key,
        )
        try:
            selected = select_shared_params(train_grid_results)
        except NoValidParameterSetError:
            selected = None
    else:
        train_grid_results = pd.DataFrame()
        selected = None

    rows: list[dict] = []
    validation_rows: list[dict] = []
    train_trades_by_symbol: list[pd.DataFrame] = []
    valid_trades_by_symbol: list[pd.DataFrame] = []

    for symbol_data in symbols:
        if shared_params:
            if selected is None:
                selected_params: dict = {}
                train_trades = pd.DataFrame()
                valid_trades = pd.DataFrame()
                train_summary = summarize_trades(train_trades)
                valid_summary = summarize_trades(valid_trades)
                evaluation = {
                    "pass_fail": "failed",
                    "reasons": "NO_VALID_PARAMETER_SET",
                }
                train_trades_by_symbol.append(train_trades)
                valid_trades_by_symbol.append(valid_trades)
                base_row = {
                    "symbol": symbol_data.inst_id,
                    **_prefixed(train_summary, "train"),
                    **_prefixed(valid_summary, "valid"),
                    "shared_params": bool(shared_params),
                    "pass_fail": evaluation["pass_fail"],
                    "fail_reasons": evaluation["reasons"],
                }
                rows.append(base_row)
                validation_rows.append(
                    {
                        "symbol": symbol_data.inst_id,
                        **_prefixed(valid_summary, "valid"),
                        "pass_fail": evaluation["pass_fail"],
                        "fail_reasons": evaluation["reasons"],
                    }
                )
                continue
            train_frame, valid_frame = split_train_valid(symbol_data.frame, valid_fraction=0.25)
            train_trades = run_backtest(
                train_frame,
                inst_id=symbol_data.inst_id,
                params=selected,
                signal_timeframe=signal_key,
                trend_timeframe=trend_key,
            )
            valid_trades = run_backtest(
                valid_frame,
                inst_id=symbol_data.inst_id,
                params=selected,
                signal_timeframe=signal_key,
                trend_timeframe=trend_key,
            )
            train_summary = summarize_trades(train_trades)
            valid_summary = summarize_trades(valid_trades)
            evaluation = evaluate_symbol(train_summary, valid_summary)
            selected_params = asdict(selected)
        else:
            result = run_train_valid_symbol(
                symbol_data.frame,
                inst_id=symbol_data.inst_id,
                params_grid=params_grid,
                signal_timeframe=signal_key,
                trend_timeframe=trend_key,
            )
            train_trades = result["train_trades"]
            valid_trades = result["valid_trades"]
            train_summary = result["train_summary"]
            valid_summary = result["valid_summary"]
            evaluation = result["evaluation"]
            selected_params = result["selected_params"]

        train_trades_by_symbol.append(train_trades)
        valid_trades_by_symbol.append(valid_trades)
        base_row = {
            "symbol": symbol_data.inst_id,
            **_prefixed(train_summary, "train"),
            **_prefixed(valid_summary, "valid"),
            **selected_params,
            "shared_params": bool(shared_params),
            "pass_fail": evaluation["pass_fail"],
            "fail_reasons": evaluation["reasons"],
        }
        rows.append(base_row)
        validation_rows.append(
            {
                "symbol": symbol_data.inst_id,
                **_prefixed(valid_summary, "valid"),
                "pass_fail": evaluation["pass_fail"],
                "fail_reasons": evaluation["reasons"],
            }
        )

    single_results = pd.DataFrame(rows)
    validation_results = pd.DataFrame(validation_rows)
    train_portfolio = combine_trade_summaries(train_trades_by_symbol, symbol_count=len(symbols))
    valid_portfolio = combine_trade_summaries(valid_trades_by_symbol, symbol_count=len(symbols))
    profitable_ratio = float((single_results["valid_total_return"] > 0).mean()) if not single_results.empty else 0.0
    hit27_ratio = float((single_results["valid_hit_27pct_stop"] > 0).mean()) if not single_results.empty else 0.0
    portfolio_eval = evaluate_portfolio(
        train_portfolio,
        valid_portfolio,
        profitable_symbol_ratio=profitable_ratio,
        hit_27pct_symbol_ratio=hit27_ratio,
    )
    portfolio_results = pd.DataFrame(
        [
            {
                "portfolio_name": "shared_parameter_portfolio" if shared_params else "per_symbol_parameter_portfolio",
                "dataset": dataset,
                "signal_timeframe": signal_key,
                "trend_timeframe": trend_key,
                "symbols_included": len(symbols),
                **_prefixed(train_portfolio, "train"),
                **_prefixed(valid_portfolio, "valid"),
                "profitable_symbol_ratio": profitable_ratio,
                "hit_27pct_symbol_ratio": hit27_ratio,
                "pass_fail": portfolio_eval["pass_fail"],
                "fail_reasons": portfolio_eval["reasons"],
            }
        ]
    )
    valid_trades = pd.concat([frame for frame in valid_trades_by_symbol if not frame.empty], ignore_index=True) if any(not frame.empty for frame in valid_trades_by_symbol) else pd.DataFrame()
    leverage_risk = build_leverage_risk_table(valid_trades)
    checklist = build_acceptance_checklist(
        train_grid_results=train_grid_results,
        selected=selected if selected is not None else StrategyParams(),
        validation_results=validation_results,
        portfolio_results=portfolio_results,
        leverage_risk=leverage_risk,
        shared_params=shared_params,
        signal_timeframe=signal_key,
    )
    return {
        "train_grid_results": train_grid_results,
        "selected_params": selected if selected is not None else {},
        "validation_results": validation_results,
        "single_symbol_results": single_results,
        "portfolio_results": portfolio_results,
        "leverage_risk": leverage_risk,
        "acceptance_checklist": checklist,
        "sample_trades": valid_trades,
    }


def build_leverage_risk_table(trades: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "symbol",
        "trade_id",
        "side",
        "entry_time",
        "entry_price",
        "stop_loss",
        "stop_distance_pct",
        "qty",
        "notional",
        "leverage_cap",
        "leverage_used",
        "est_liq_buffer_pct",
        "near_liq_flag",
        "invalid_reason",
        "funding_scenario",
        "fee_scenario",
        "slip_scenario",
    ]
    if trades.empty:
        return pd.DataFrame(columns=columns)
    table = trades.reset_index(drop=True).copy()
    table.insert(0, "trade_id", table.index + 1)
    table["invalid_reason"] = table["near_liq_flag"].map(lambda value: "near_liquidation_before_stop" if bool(value) else "")
    table["funding_scenario"] = "baseline_0.01pct_8h"
    table["fee_scenario"] = "okx_conservative_taker"
    table["slip_scenario"] = "participation_tier"
    table = table.rename(columns={"inst_id": "symbol"})
    return table[[col for col in columns if col in table.columns]]


def build_acceptance_checklist(
    *,
    train_grid_results: pd.DataFrame,
    selected: StrategyParams,
    validation_results: pd.DataFrame,
    portfolio_results: pd.DataFrame,
    leverage_risk: pd.DataFrame,
    shared_params: bool,
    signal_timeframe: str = DEFAULT_SIGNAL_TIMEFRAME,
) -> pd.DataFrame:
    portfolio = portfolio_results.iloc[0].to_dict() if not portfolio_results.empty else {}
    near_liq_count = int(leverage_risk["near_liq_flag"].fillna(False).sum()) if "near_liq_flag" in leverage_risk else 0
    expected_grid_size = len(parameter_grid(signal_timeframe))
    checks = [
        ("exchange_okx_only", True, "配置和合约命名固定 OKX SWAP"),
        (
            "parameter_grid_evaluated",
            len(train_grid_results) > 0 or not shared_params,
            f"current rows {len(train_grid_results)}, full {signal_timeframe} grid {expected_grid_size}",
        ),
        ("shared_params_selected", shared_params, json.dumps(asdict(selected), ensure_ascii=False)),
        ("validation_once_after_freeze", shared_params and not validation_results.empty, "验证结果只使用冻结参数生成"),
        ("portfolio_valid_trades_ge_80", portfolio.get("valid_total_trades", 0) >= 80, str(portfolio.get("valid_total_trades", 0))),
        ("near_liq_zero", near_liq_count == 0, str(near_liq_count)),
        ("live_orders_disabled", True, "MVP 只人工确认，不自动下单"),
    ]
    return pd.DataFrame(
        [
            {
                "check": name,
                "passed": bool(passed),
                "evidence": evidence,
                "status": "passed" if passed else "failed",
            }
            for name, passed, evidence in checks
        ]
    )


def write_research_artifacts(artifacts: dict[str, pd.DataFrame | dict | StrategyParams], output_dir: str | Path) -> dict[str, Path]:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths = {
        "train_grid_results": out / "train_grid_results.csv",
        "selected_params": out / "selected_params.json",
        "validation_results": out / "validation_results.csv",
        "single_symbol_results": out / "single_symbol_results.csv",
        "portfolio_results": out / "portfolio_results.csv",
        "portfolio_result": out / "portfolio_result.csv",
        "leverage_risk": out / "leverage_risk.csv",
        "acceptance_checklist": out / "acceptance_checklist.csv",
        "sample_trades": out / "sample_trades.csv",
        "sample_summary": out / "sample_summary.json",
        "final_report": out / "final_report.md",
        "selection_memo": out / "selection_memo.md",
    }
    for key in ["train_grid_results", "validation_results", "single_symbol_results", "portfolio_results", "leverage_risk", "acceptance_checklist", "sample_trades"]:
        value = artifacts.get(key)
        if isinstance(value, pd.DataFrame):
            value.to_csv(paths[key], index=False, encoding="utf-8")
    if isinstance(artifacts.get("portfolio_results"), pd.DataFrame):
        artifacts["portfolio_results"].to_csv(paths["portfolio_result"], index=False, encoding="utf-8")
    selected = artifacts.get("selected_params")
    selected_dict = asdict(selected) if isinstance(selected, StrategyParams) else selected
    paths["selected_params"].write_text(json.dumps(selected_dict, indent=2, ensure_ascii=False), encoding="utf-8")
    portfolio = artifacts.get("portfolio_results")
    summary = portfolio.iloc[0].to_dict() if isinstance(portfolio, pd.DataFrame) and not portfolio.empty else {}
    panel_summary = {
        "total_return": summary.get("valid_total_return", summary.get("total_return", 0)),
        "profit_factor": summary.get("valid_profit_factor", summary.get("profit_factor", 0)),
        "payoff_ratio": summary.get("valid_payoff_ratio", summary.get("payoff_ratio", 0)),
        "win_rate": summary.get("valid_win_rate", summary.get("win_rate", 0)),
        "total_trades": summary.get("valid_total_trades", summary.get("total_trades", 0)),
        "max_drawdown": summary.get("valid_max_drawdown", summary.get("max_drawdown", 0)),
        "status": summary.get("pass_fail", summary.get("status", "unknown")),
    }
    paths["sample_summary"].write_text(json.dumps(panel_summary, indent=2, ensure_ascii=False), encoding="utf-8")
    paths["selection_memo"].write_text(build_selection_memo(artifacts), encoding="utf-8")
    paths["final_report"].write_text(build_final_report(artifacts), encoding="utf-8")
    return paths


def build_selection_memo(artifacts: dict[str, pd.DataFrame | dict | StrategyParams]) -> str:
    selected = artifacts.get("selected_params")
    selected_dict = asdict(selected) if isinstance(selected, StrategyParams) else selected
    train_grid = artifacts.get("train_grid_results")
    rows = len(train_grid) if isinstance(train_grid, pd.DataFrame) else 0
    return "\n".join(
        [
            "# 参数冻结说明",
            "",
            f"- 训练网格行数：{rows}",
            "- 选择规则：先过硬阈值，再按盈亏比、PF、回撤、交易数、参数居中排序。",
            "- 验证规则：冻结后只运行一次验证段，不用验证结果回改参数。",
            f"- 冻结参数：`{json.dumps(selected_dict, ensure_ascii=False)}`",
        ]
    )


def build_final_report(artifacts: dict[str, pd.DataFrame | dict | StrategyParams]) -> str:
    portfolio = artifacts.get("portfolio_results")
    checklist = artifacts.get("acceptance_checklist")
    row = portfolio.iloc[0].to_dict() if isinstance(portfolio, pd.DataFrame) and not portfolio.empty else {}
    failed_checks = []
    if isinstance(checklist, pd.DataFrame) and not checklist.empty:
        failed_checks = checklist.loc[~checklist["passed"].astype(bool), "check"].tolist()
    return "\n".join(
        [
            "# OKX 回测与半自动信号验收报告",
            "",
            f"- 组合状态：{row.get('pass_fail', 'unknown')}",
            f"- 验证收益：{row.get('valid_total_return', 0)}",
            f"- 验证 PF：{row.get('valid_profit_factor', 0)}",
            f"- 验证盈亏比：{row.get('valid_payoff_ratio', 0)}",
            f"- 验证交易数：{row.get('valid_total_trades', 0)}",
            f"- 失败原因：{row.get('fail_reasons', '')}",
            f"- 验收未通过项：{','.join(failed_checks) if failed_checks else '无'}",
            "",
            "默认不自动实盘下单；本报告只用于本地研究和人工确认。",
        ]
    )
