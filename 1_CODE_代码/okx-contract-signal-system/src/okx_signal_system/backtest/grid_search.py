from __future__ import annotations

from dataclasses import asdict
from itertools import product

import pandas as pd

from okx_signal_system.backtest.runner import run_backtest_from_features, summarize_trades, validate_backtest_result
from okx_signal_system.features.indicators import build_feature_frame
from okx_signal_system.strategy.trend_breakout import StrategyParams
from okx_signal_system.strategy.vote_gate import DEFAULT_MIN_VOTE_APPROVAL_RATE
from okx_signal_system.timeframe import normalize_timeframe


def _finite_metric(value: object) -> bool:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return False
    return pd.notna(numeric) and numeric not in {float("inf"), float("-inf")}


def parameter_grid(timeframe: str = "15m") -> list[StrategyParams]:
    tf = normalize_timeframe(timeframe)
    if tf == "15m":
        fast_values = [72, 96, 120]
        slow_values = [576, 720, 960]
        breakout_values = [288, 384, 480]
        atr_values = [4.0, 4.5]
        tp_values = [6.0, 7.0]
        hold_values = [576, 768]
    elif tf == "5m":
        fast_values = [18, 24, 36]
        slow_values = [72, 96, 144]
        breakout_values = [48, 72, 96]
        atr_values = [2.0, 2.4, 2.8]
        tp_values = [3.5, 4.0, 5.0, 6.0]
        hold_values = [96, 144, 192, 288]
    else:
        fast_values = [10, 20, 30]
        slow_values = [50, 60, 80]
        breakout_values = [20, 40, 60]
        atr_values = [1.5, 2.0, 2.5, 3.0]
        tp_values = [3.5, 4.0, 5.0, 6.0]
        hold_values = [24, 48, 72]
    return [
        StrategyParams(
            fast_ema=fast,
            slow_ema=slow,
            breakout_window=breakout,
            atr_stop_mult=atr_mult,
            take_profit_mult=tp_mult,
            max_hold_bars=max_hold,
        )
        for fast, slow, breakout, atr_mult, tp_mult, max_hold in product(
            fast_values,
            slow_values,
            breakout_values,
            atr_values,
            tp_values,
            hold_values,
        )
    ]


def run_grid_search(
    frame: pd.DataFrame,
    *,
    inst_id: str,
    params_grid: list[StrategyParams] | None = None,
    signal_timeframe: str = "15m",
    trend_timeframe: str | None = None,
    min_vote_approval_rate: float = DEFAULT_MIN_VOTE_APPROVAL_RATE,
    trade_start: pd.Timestamp | None = None,
    trade_end: pd.Timestamp | None = None,
) -> pd.DataFrame:
    rows = []
    feature_cache: dict[tuple[int, int, int, int], pd.DataFrame] = {}
    for params in params_grid or parameter_grid(signal_timeframe):
        feature_key = (params.fast_ema, params.slow_ema, params.breakout_window, params.atr_window)
        if feature_key not in feature_cache:
            feature_cache[feature_key] = build_feature_frame(
                frame,
                fast_ema=params.fast_ema,
                slow_ema=params.slow_ema,
                breakout_window=params.breakout_window,
                atr_window=params.atr_window,
                signal_timeframe=signal_timeframe,
                trend_timeframe=trend_timeframe,
            )
        try:
            raw_trades = run_backtest_from_features(
                feature_cache[feature_key],
                inst_id=inst_id,
                params=params,
                min_vote_approval_rate=min_vote_approval_rate,
            )
            if (trade_start is not None or trade_end is not None) and "entry_time" in raw_trades.columns:
                entry_time = pd.to_datetime(raw_trades["entry_time"], utc=True, errors="coerce")
                if trade_start is not None:
                    raw_trades = raw_trades[entry_time >= pd.Timestamp(trade_start)]
                    entry_time = pd.to_datetime(raw_trades["entry_time"], utc=True, errors="coerce")
                if trade_end is not None:
                    raw_trades = raw_trades[entry_time <= pd.Timestamp(trade_end)]
            trades = validate_backtest_result(raw_trades, context="grid_search")
        except ValueError:
            continue
        summary = summarize_trades(trades)
        rows.append({**asdict(params), **{f"train_{key}": value for key, value in summary.items()}})
    return pd.DataFrame(rows)


def select_best_params(grid_results: pd.DataFrame, *, min_total_trades: int = 1) -> StrategyParams:
    """
    参数选择策略：以盈亏比(Profit Factor)为主，胜率为辅助参考

    核心原则：
    1. 盈亏比是衡量策略质量的核心指标
    2. 胜率只是辅助参考，高盈亏比即使胜率低也可以
    3. 在盈亏比相同的情况下选择胜率更高的
    """
    if grid_results.empty:
        raise ValueError("grid results are empty")
    ranked = grid_results.copy()
    ranked = ranked[
        ranked["train_profit_factor"].map(_finite_metric)
        & (ranked["train_total_trades"].fillna(0).astype(float) >= float(min_total_trades))
    ].copy()
    if ranked.empty:
        raise ValueError("no finite grid result passed the selection gates")

    # 标准化盈亏比（处理inf情况）
    ranked["rank_pf"] = ranked["train_profit_factor"].astype(float)

    # 按盈亏比为主、胜率为辅排序
    ranked = ranked.sort_values(
        ["train_status", "rank_pf", "train_win_rate", "train_total_return", "train_total_trades"],
        ascending=[False, False, False, False, False],
    )
    row = ranked.iloc[0]
    return StrategyParams(
        fast_ema=int(row["fast_ema"]),
        slow_ema=int(row["slow_ema"]),
        breakout_window=int(row["breakout_window"]),
        atr_stop_mult=float(row["atr_stop_mult"]),
        take_profit_mult=float(row["take_profit_mult"]),
        max_hold_bars=int(row["max_hold_bars"]),
        atr_window=int(row.get("atr_window", 14)),
    )
