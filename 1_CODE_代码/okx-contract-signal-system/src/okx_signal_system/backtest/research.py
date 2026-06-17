from __future__ import annotations

import json
import logging
import math
import hashlib
import subprocess
import sqlite3
from dataclasses import replace
from dataclasses import asdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import pandas as pd

from okx_signal_system.backtest.evaluation import evaluate_portfolio, evaluate_symbol
from okx_signal_system.backtest.grid_search import parameter_grid, run_grid_search, select_best_params
from okx_signal_system.backtest.runner import run_backtest, split_train_valid, summarize_trades, validate_backtest_result
from okx_signal_system.config import project_paths
from okx_signal_system.data.loader import SymbolData, load_all_symbols
from okx_signal_system.risk.costs import CostConfig, estimate_costs
from okx_signal_system.strategy.trend_breakout import StrategyParams
from okx_signal_system.timeframe import default_trend_timeframe, timeframe_spec

log = logging.getLogger(__name__)

BASELINE_PARAMS = StrategyParams()
DEFAULT_DATASET = "okx_15m_extended"
DEFAULT_SIGNAL_TIMEFRAME = "15m"
DEFAULT_TREND_TIMEFRAME = "1h"


PARAM_COLS = [
    "fast_ema",
    "slow_ema",
    "breakout_window",
    "atr_stop_mult",
    "take_profit_mult",
    "max_hold_bars",
    "atr_window",
]


@dataclass(frozen=True)
class ResearchValidationConfig:
    train_fraction: float = 0.60
    validation_fraction: float = 0.25
    purge_bars: int = 768
    embargo_bars: int = 96
    min_train_trades: int = 80
    min_neighbor_count: int = 3
    min_neighbor_pf_ratio: float = 0.85
    min_neighbor_ratio: float = 0.50
    neighbor_distance: float = 1.5


@dataclass(frozen=True)
class EvaluationWindow:
    frame_with_warmup: pd.DataFrame
    trade_start: pd.Timestamp
    trade_end: pd.Timestamp


@dataclass(frozen=True)
class ResearchSplit:
    train: pd.DataFrame
    validation: pd.DataFrame
    blind: pd.DataFrame
    validation_window: EvaluationWindow
    blind_window: EvaluationWindow
    purge_bars: int
    embargo_bars: int
    boundaries: dict[str, pd.Timestamp | None]


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
        raise ValueError("no valid backtest rows for combined summary")
    combined = pd.concat(frames, ignore_index=True).sort_values("exit_time").reset_index(drop=True)
    validate_backtest_result(combined, context="combined_summary")
    denominator_symbols = max(1, symbol_count or combined["inst_id"].nunique())
    return summarize_trades(combined, initial_equity=initial_equity_per_symbol * denominator_symbols)


def _scenario_cost_config(base: CostConfig, *, multiplier: float, funding_rate: float) -> CostConfig:
    return replace(
        base,
        taker_fee_rate=float(base.taker_fee_rate) * multiplier,
        maker_fee_rate=float(base.maker_fee_rate) * multiplier,
        funding_rate=funding_rate,
    )


def _stress_slippage_bps(base: CostConfig, multiplier: float) -> float:
    if multiplier <= 1.0:
        return float(base.normal_slippage_bps)
    return max(float(base.stress_slippage_bps), float(base.normal_slippage_bps) * multiplier)


def _recompute_costs_from_trade_facts(
    trades: pd.DataFrame,
    *,
    cost_config: CostConfig,
    slippage_bps: float,
    fallback_multiplier: float,
) -> pd.DataFrame:
    stressed = trades.copy()
    for column in ["entry_fee", "exit_fee", "slippage_cost", "funding_fee", "costs"]:
        if column not in stressed.columns:
            stressed[column] = 0.0

    def _numeric_row_value(row: pd.Series, key: str) -> float:
        return float(pd.to_numeric(pd.Series([row.get(key, 0.0)]), errors="coerce").fillna(0.0).iloc[0])

    required = {"entry_price", "exit_price", "qty", "entry_time", "exit_time"}
    can_recompute = required.issubset(stressed.columns)
    entry_fees: list[float] = []
    exit_fees: list[float] = []
    slippage_costs: list[float] = []
    funding_fees: list[float] = []
    total_costs: list[float] = []
    sources: list[str] = []
    for _, row in stressed.iterrows():
        try:
            if not can_recompute:
                raise ValueError("missing_trade_facts")
            costs = estimate_costs(
                entry_price=float(row["entry_price"]),
                exit_price=float(row["exit_price"]),
                qty=float(row["qty"]),
                entry_time=pd.Timestamp(row["entry_time"]),
                exit_time=pd.Timestamp(row["exit_time"]),
                config=cost_config,
                slippage_bps=slippage_bps,
            )
            entry_fee = float(costs.entry_fee)
            exit_fee = float(costs.exit_fee)
            slippage_cost = float(costs.slippage_cost)
            funding_fee = float(costs.funding_fee)
            source = "trade_fact_recompute"
        except (TypeError, ValueError):
            entry_fee = _numeric_row_value(row, "entry_fee") * fallback_multiplier
            exit_fee = _numeric_row_value(row, "exit_fee") * fallback_multiplier
            slippage_cost = _numeric_row_value(row, "slippage_cost") * fallback_multiplier
            funding_fee = _numeric_row_value(row, "funding_fee") * fallback_multiplier
            if entry_fee == 0.0 and exit_fee == 0.0 and slippage_cost == 0.0 and funding_fee == 0.0:
                total = _numeric_row_value(row, "costs") * fallback_multiplier
                entry_fee = total
            source = "legacy_cost_fallback"
        total_cost = entry_fee + exit_fee + slippage_cost + funding_fee
        entry_fees.append(entry_fee)
        exit_fees.append(exit_fee)
        slippage_costs.append(slippage_cost)
        funding_fees.append(funding_fee)
        total_costs.append(total_cost)
        sources.append(source)

    gross_pnl = pd.to_numeric(stressed.get("gross_pnl", 0.0), errors="coerce").fillna(0.0)
    risk_amount = pd.to_numeric(stressed.get("risk_amount", 0.0), errors="coerce").replace(0, pd.NA)
    stressed["entry_fee"] = entry_fees
    stressed["exit_fee"] = exit_fees
    stressed["slippage_cost"] = slippage_costs
    stressed["funding_fee"] = funding_fees
    stressed["costs"] = total_costs
    stressed["net_pnl"] = gross_pnl - stressed["costs"]
    stressed["net_r"] = (stressed["net_pnl"] / risk_amount).fillna(0.0)
    stressed["final_net_r"] = stressed["net_r"]
    stressed["cost_recompute_source"] = sources
    return stressed


def replay_cost_stress(
    trades: pd.DataFrame,
    *,
    cost_config: CostConfig | None = None,
    initial_equity: float = 10000.0,
) -> pd.DataFrame:
    if cost_config is None:
        from okx_signal_system.config import load_runtime_config

        cost_config = load_runtime_config().cost_config()
    columns = [
        "scenario",
        "cost_multiplier",
        "funding_rate",
        "slippage_bps",
        "net_r",
        "profit_factor",
        "max_drawdown",
        "total_trades",
        "long_trades",
        "short_trades",
        "top_symbol",
        "top_symbol_net_r_share",
        "top_regime",
        "entry_fee",
        "exit_fee",
        "slippage_cost",
        "funding_fee",
        "funding_sensitivity",
        "recompute_source",
    ]
    if trades.empty:
        return pd.DataFrame(columns=columns)

    rows: list[dict] = []
    scenarios = [
        ("baseline", 1.0, float(cost_config.funding_rate)),
        ("stress_1_5x", 1.5, float(cost_config.funding_rate) * 1.5),
        ("stress_2x", 2.0, float(cost_config.funding_rate) * 2.0),
    ]
    baseline_net_r: float | None = None
    baseline_funding_fee: float | None = None
    for name, multiplier, funding_rate in scenarios:
        slippage_bps = _stress_slippage_bps(cost_config, multiplier)
        scenario_config = _scenario_cost_config(cost_config, multiplier=multiplier, funding_rate=funding_rate)
        stressed = _recompute_costs_from_trade_facts(
            trades,
            cost_config=scenario_config,
            slippage_bps=slippage_bps,
            fallback_multiplier=multiplier,
        )
        summary = summarize_trades(stressed, initial_equity=initial_equity)
        total_net_r = float(stressed["net_r"].sum())
        funding_fee = float(pd.to_numeric(stressed["funding_fee"], errors="coerce").fillna(0.0).sum())
        risk_total = float(pd.to_numeric(stressed.get("risk_amount", 0.0), errors="coerce").fillna(0.0).sum())
        if baseline_net_r is None:
            baseline_net_r = total_net_r
            baseline_funding_fee = funding_fee
        symbol_r = stressed.groupby("inst_id")["net_r"].sum() if "inst_id" in stressed else pd.Series(dtype=float)
        if symbol_r.empty:
            top_symbol = ""
            top_symbol_share = 0.0
        else:
            top_symbol = str(symbol_r.abs().idxmax())
            top_symbol_share = float(abs(symbol_r.loc[top_symbol]) / symbol_r.abs().sum()) if symbol_r.abs().sum() else 0.0
        if "market_regime" in stressed:
            regime_r = stressed.groupby("market_regime")["net_r"].sum()
            top_regime = str(regime_r.abs().idxmax()) if not regime_r.empty else "unknown"
        else:
            top_regime = "unknown"
        recompute_source = ",".join(sorted({str(value) for value in stressed["cost_recompute_source"].unique()}))
        rows.append(
            {
                "scenario": name,
                "cost_multiplier": multiplier,
                "funding_rate": funding_rate,
                "slippage_bps": slippage_bps,
                "net_r": total_net_r,
                "profit_factor": summary["profit_factor"],
                "max_drawdown": summary["max_drawdown"],
                "total_trades": summary["total_trades"],
                "long_trades": int((stressed["side"] == "long").sum()) if "side" in stressed else 0,
                "short_trades": int((stressed["side"] == "short").sum()) if "side" in stressed else 0,
                "top_symbol": top_symbol,
                "top_symbol_net_r_share": top_symbol_share,
                "top_regime": top_regime,
                "entry_fee": float(pd.to_numeric(stressed["entry_fee"], errors="coerce").fillna(0.0).sum()),
                "exit_fee": float(pd.to_numeric(stressed["exit_fee"], errors="coerce").fillna(0.0).sum()),
                "slippage_cost": float(pd.to_numeric(stressed["slippage_cost"], errors="coerce").fillna(0.0).sum()),
                "funding_fee": funding_fee,
                "funding_sensitivity": -((funding_fee - float(baseline_funding_fee or 0.0)) / risk_total) if risk_total else 0.0,
                "recompute_source": recompute_source,
            }
        )
    return pd.DataFrame(rows, columns=columns)


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


def _max_warmup_bars(params_grid: list[StrategyParams] | None, signal_timeframe: str) -> int:
    grid = params_grid or parameter_grid(signal_timeframe)
    if not grid:
        base = StrategyParams()
        return max(base.slow_ema, base.breakout_window, base.max_hold_bars) + base.atr_window
    return max(max(params.slow_ema, params.breakout_window, params.max_hold_bars) + params.atr_window for params in grid)


def _timestamp_bounds(symbols: list[SymbolData]) -> tuple[pd.Timestamp, pd.Timestamp]:
    starts: list[pd.Timestamp] = []
    ends: list[pd.Timestamp] = []
    for symbol_data in symbols:
        frame = symbol_data.frame
        if frame.empty or "ts" not in frame.columns:
            raise ValueError(f"{symbol_data.inst_id} has no timestamped bars")
        ts = pd.to_datetime(frame["ts"], utc=True, errors="coerce").dropna()
        if ts.empty:
            raise ValueError(f"{symbol_data.inst_id} has no valid timestamps")
        starts.append(ts.min())
        ends.append(ts.max())
    return max(starts), min(ends)


def _global_split_boundaries(
    symbols: list[SymbolData],
    *,
    common_start: pd.Timestamp,
    common_end: pd.Timestamp,
    warmup: int,
    signal_timeframe: str,
    config: ResearchValidationConfig,
) -> dict[str, pd.Timestamp]:
    spec = timeframe_spec(signal_timeframe)
    axis = pd.date_range(common_start, common_end, freq=spec.pandas_freq, tz="UTC")
    if len(axis) <= warmup + config.purge_bars * 2 + config.embargo_bars * 2 + 10:
        raise ValueError("STRICT_SPLIT_UNAVAILABLE: insufficient common calendar axis")
    usable_axis = axis[warmup:]
    train_cut = int(len(usable_axis) * config.train_fraction)
    valid_cut = int(len(usable_axis) * (config.train_fraction + config.validation_fraction))
    valid_start_idx = train_cut + config.purge_bars
    blind_start_idx = valid_cut + config.embargo_bars
    if train_cut <= 0 or valid_start_idx >= valid_cut or blind_start_idx >= len(usable_axis):
        raise ValueError("STRICT_SPLIT_UNAVAILABLE: empty strict split window")
    return {
        "common_start": common_start,
        "common_end": common_end,
        "train_start": pd.Timestamp(usable_axis[0]),
        "train_end": pd.Timestamp(usable_axis[train_cut - 1]),
        "validation_start": pd.Timestamp(usable_axis[valid_start_idx]),
        "validation_end": pd.Timestamp(usable_axis[valid_cut - 1]),
        "blind_start": pd.Timestamp(usable_axis[blind_start_idx]),
        "blind_end": pd.Timestamp(usable_axis[-1]),
    }


def common_calendar_split(
    symbols: list[SymbolData],
    *,
    params_grid: list[StrategyParams] | None = None,
    signal_timeframe: str = DEFAULT_SIGNAL_TIMEFRAME,
    config: ResearchValidationConfig = ResearchValidationConfig(),
) -> dict[str, ResearchSplit]:
    if not symbols:
        raise ValueError("no symbols loaded for research")
    if not 0 < config.train_fraction < 1 or not 0 < config.validation_fraction < 1:
        raise ValueError("research split fractions must be between 0 and 1")
    if config.train_fraction + config.validation_fraction >= 1:
        raise ValueError("research split must reserve a blind test set")

    common_start, common_end = _timestamp_bounds(symbols)
    warmup = _max_warmup_bars(params_grid, signal_timeframe)
    boundaries = _global_split_boundaries(
        symbols,
        common_start=common_start,
        common_end=common_end,
        warmup=warmup,
        signal_timeframe=signal_timeframe,
        config=config,
    )
    split_by_symbol: dict[str, ResearchSplit] = {}
    for symbol_data in symbols:
        frame = symbol_data.frame.copy()
        frame["ts"] = pd.to_datetime(frame["ts"], utc=True, errors="coerce")
        frame = frame[(frame["ts"] >= common_start) & (frame["ts"] <= common_end)].sort_values("ts").reset_index(drop=True)
        train = frame[(frame["ts"] >= boundaries["train_start"]) & (frame["ts"] <= boundaries["train_end"])].reset_index(drop=True)
        validation = frame[(frame["ts"] >= boundaries["validation_start"]) & (frame["ts"] <= boundaries["validation_end"])].reset_index(drop=True)
        blind = frame[(frame["ts"] >= boundaries["blind_start"]) & (frame["ts"] <= boundaries["blind_end"])].reset_index(drop=True)
        if train.empty or validation.empty or blind.empty:
            raise ValueError(f"STRICT_SPLIT_UNAVAILABLE: {symbol_data.inst_id} has empty train/validation/blind split")
        validation_warmup_start = boundaries["validation_start"] - pd.Timedelta(minutes=timeframe_spec(signal_timeframe).minutes * warmup)
        blind_warmup_start = boundaries["blind_start"] - pd.Timedelta(minutes=timeframe_spec(signal_timeframe).minutes * warmup)
        validation_with_warmup = frame[
            (frame["ts"] >= validation_warmup_start) & (frame["ts"] <= boundaries["validation_end"])
        ].reset_index(drop=True)
        blind_with_warmup = frame[
            (frame["ts"] >= blind_warmup_start) & (frame["ts"] <= boundaries["blind_end"])
        ].reset_index(drop=True)
        if validation_with_warmup.empty or blind_with_warmup.empty:
            raise ValueError(f"STRICT_SPLIT_UNAVAILABLE: {symbol_data.inst_id} has empty evaluation warmup window")

        split_by_symbol[symbol_data.inst_id] = ResearchSplit(
            train=train,
            validation=validation,
            blind=blind,
            validation_window=EvaluationWindow(
                frame_with_warmup=validation_with_warmup,
                trade_start=boundaries["validation_start"],
                trade_end=boundaries["validation_end"],
            ),
            blind_window=EvaluationWindow(
                frame_with_warmup=blind_with_warmup,
                trade_start=boundaries["blind_start"],
                trade_end=boundaries["blind_end"],
            ),
            purge_bars=config.purge_bars,
            embargo_bars=config.embargo_bars,
            boundaries=dict(boundaries),
        )
    return split_by_symbol


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
    train_trades = validate_backtest_result(
        run_backtest(
            train_frame,
            inst_id=inst_id,
            params=selected,
            signal_timeframe=signal_key,
            trend_timeframe=trend_key,
        ),
        context=f"{inst_id} train",
    )
    valid_trades = validate_backtest_result(
        run_backtest(
            valid_frame,
            inst_id=inst_id,
            params=selected,
            signal_timeframe=signal_key,
            trend_timeframe=trend_key,
        ),
        context=f"{inst_id} validation",
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


def _finite_number(value: object) -> bool:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(numeric)


def _parameter_step_sizes(frame: pd.DataFrame) -> dict[str, float]:
    steps: dict[str, float] = {}
    for col in PARAM_COLS:
        values = sorted({float(value) for value in frame[col].dropna().unique()})
        deltas = [round(values[idx + 1] - values[idx], 12) for idx in range(len(values) - 1) if values[idx + 1] > values[idx]]
        steps[col] = min(deltas) if deltas else 1.0
    return steps


def _neighbor_stability(frame: pd.DataFrame, *, config: ResearchValidationConfig) -> pd.DataFrame:
    if frame.empty:
        return frame
    out = frame.copy()
    steps = _parameter_step_sizes(out)
    stable_counts: list[int] = []
    stable_ratios: list[float] = []
    for _, row in out.iterrows():
        if not bool(row.get("base_train_gate", False)) or not _finite_number(row.get("train_profit_factor")):
            stable_counts.append(0)
            stable_ratios.append(0.0)
            continue
        distance = pd.Series(0.0, index=out.index)
        for col in PARAM_COLS:
            step = steps[col] if steps[col] > 0 else 1.0
            distance += (out[col].astype(float) - float(row[col])).abs() / step
        mask = distance <= float(config.neighbor_distance)
        mask &= out.index != row.name
        mask &= out["base_train_gate"].fillna(False).astype(bool)
        neighbors = out[mask].copy()
        if neighbors.empty:
            stable_counts.append(0)
            stable_ratios.append(0.0)
            continue
        threshold = float(row["train_profit_factor"]) * config.min_neighbor_pf_ratio
        stable = neighbors[
            neighbors["train_profit_factor"].map(_finite_number)
            & (neighbors["train_profit_factor"].astype(float) >= threshold)
            & (neighbors["train_total_trades"].astype(float) >= config.min_train_trades)
        ]
        stable_counts.append(int(len(stable)))
        stable_ratios.append(float(len(stable) / len(neighbors)))
    out["stable_neighbor_count"] = stable_counts
    out["stable_neighbor_ratio"] = stable_ratios
    return out


def _safe_portfolio_pf(wins: float, losses: float) -> float:
    return float(wins / abs(losses)) if losses < 0 else float("inf")


def _contribution_concentration(values: pd.Series) -> float:
    numeric = pd.to_numeric(values, errors="coerce").fillna(0.0).abs()
    total = float(numeric.sum())
    return float(numeric.max() / total) if total else 0.0


def run_shared_train_grid(
    symbols: list[SymbolData],
    *,
    params_grid: list[StrategyParams] | None = None,
    signal_timeframe: str | None = None,
    trend_timeframe: str | None = None,
    min_vote_approval_rate: float = 0.40,
    splits: dict[str, ResearchSplit] | None = None,
    validation_config: ResearchValidationConfig = ResearchValidationConfig(),
) -> pd.DataFrame:
    signal_key, trend_key = _resolve_timeframes(symbols[0].frame, signal_timeframe, trend_timeframe)
    grid = params_grid or parameter_grid(signal_key)
    all_rows: list[dict] = []
    for symbol_data in symbols:
        train_frame = splits[symbol_data.inst_id].train if splits and symbol_data.inst_id in splits else split_train_valid(symbol_data.frame, valid_fraction=0.25)[0]
        symbol_grid = run_grid_search(
            train_frame,
            inst_id=symbol_data.inst_id,
            params_grid=grid,
            signal_timeframe=signal_key,
            trend_timeframe=trend_key,
            min_vote_approval_rate=min_vote_approval_rate,
        )
        symbol_grid.insert(0, "symbol", symbol_data.inst_id)
        all_rows.extend(symbol_grid.to_dict("records"))
    symbol_results = pd.DataFrame(all_rows)
    if symbol_results.empty:
        return symbol_results

    grouped = symbol_results.groupby(PARAM_COLS, dropna=False)
    agg = (
        symbol_results.groupby(PARAM_COLS, dropna=False)
        .agg(
            train_net_pnl=("train_net_pnl", "sum"),
            train_winning_net_pnl=("train_winning_net_pnl", "sum"),
            train_losing_net_pnl=("train_losing_net_pnl", "sum"),
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
            median_symbol_pf=("train_profit_factor", "median"),
            min_symbol_pf=("train_profit_factor", "min"),
            symbols_tested=("symbol", "nunique"),
        )
        .reset_index()
    )
    concentration = (
        grouped["train_net_pnl"]
        .apply(_contribution_concentration)
        .rename("top_symbol_net_pnl_share")
        .reset_index()
    )
    agg = agg.merge(concentration, on=PARAM_COLS, how="left")
    agg["train_total_return"] = agg["train_net_pnl"] / (10000.0 * agg["symbols_tested"].clip(lower=1))
    agg["train_profit_factor"] = [
        _safe_portfolio_pf(float(wins), float(losses))
        for wins, losses in zip(agg["train_winning_net_pnl"], agg["train_losing_net_pnl"], strict=False)
    ]
    agg["profitable_symbol_ratio"] = agg["profitable_symbols"] / agg["symbols_tested"].clip(lower=1)
    agg["centrality_distance"] = agg.apply(_param_distance, axis=1)
    agg["finite_profit_factor"] = agg["train_profit_factor"].map(_finite_number)
    agg["base_train_gate"] = (
        (agg["train_total_return"] > 0)
        & agg["finite_profit_factor"]
        & (agg["train_profit_factor"] >= 1.05)
        & (agg["train_payoff_ratio"] >= 1.20)
        & (agg["train_total_trades"] >= validation_config.min_train_trades)
        & (agg["train_max_drawdown"] <= 0.20)
        & (agg["train_near_liq_trades"] == 0)
        & (agg["train_gt5x_trade_pct"] <= 0.15)
        & (agg["train_pnl_share_from_gt5x"] <= 0.30)
        & (agg["profitable_symbol_ratio"] >= 0.55)
    )
    agg = _neighbor_stability(agg, config=validation_config)
    agg["passed_train_gate"] = (
        agg["base_train_gate"]
        & (agg["stable_neighbor_count"] >= validation_config.min_neighbor_count)
        & (agg["stable_neighbor_ratio"] >= validation_config.min_neighbor_ratio)
    )
    return agg


def select_shared_params(train_grid_results: pd.DataFrame) -> StrategyParams:
    """
    鍙傛暟閫夋嫨绛栫暐锛氫互鐩堜簭姣斾负涓伙紝鑳滅巼涓鸿緟鍔╁弬鑰?

    鏍稿績鍘熷垯锛?
    1. 鐩堜簭姣?Profit Factor) >= 1.05 鏄熀纭€闂ㄦ
    2. 浼樺厛閫夋嫨鐩堜簭姣旀渶楂樼殑鍙傛暟缁勫悎
    3. 鍦ㄧ泩浜忔瘮鐩歌繎鐨勬儏鍐典笅锛岄€夋嫨鑳滅巼鏇撮珮鐨?
    4. 鏈€缁堢敤楠岃瘉娈垫暟鎹獙璇佺ǔ瀹氭€?
    """
    if train_grid_results.empty:
        raise ValueError("shared train grid is empty")
    candidates = train_grid_results[train_grid_results["passed_train_gate"]].copy()
    if candidates.empty:
        raise NoValidParameterSetError("NO_VALID_PARAMETER_SET")

    # 鏍囧噯鍖朠F鐢ㄤ簬鎺掑簭锛坕nf鏇挎崲涓轰竴涓ぇ鏁帮級
    candidates = candidates[candidates["train_profit_factor"].map(_finite_number)].copy()
    if candidates.empty:
        raise NoValidParameterSetError("NO_FINITE_PROFIT_FACTOR")
    candidates["rank_pf"] = candidates["train_profit_factor"].astype(float)

    # 鎸夌泩浜忔瘮鎺掑簭锛岀泩浜忔瘮鐩稿悓鍒欐寜鑳滅巼鎺掑簭
    candidates = candidates.sort_values(
        [
            "passed_train_gate",      # 1. 棣栧厛閫氳繃闂ㄦ帶
            "rank_pf",                # 2. 鐩堜簭姣旀渶楂樹紭鍏?
            "train_win_rate",          # 3. 鑳滅巼娆′紭鍏?
            "train_max_drawdown",      # 4. 鍥炴挙鏈€灏?
            "train_total_trades",      # 5. 浜ゆ槗鏁拌冻澶熷
            "centrality_distance",     # 6. 鍙傛暟灞呬腑
        ],
        ascending=[False, False, False, True, False, True],
    )

    # 鑾峰彇鏈€浼樺弬鏁?
    row = candidates.iloc[0]

    # 璁＄畻璇ュ弬鏁扮殑缁煎悎璇勫垎锛堢敤浜庤瘖鏂級
    score = _calculate_param_score(row)
    log.info(f"鍙傛暟閫夋嫨: PF={row['train_profit_factor']:.2f}, 鑳滅巼={row['train_win_rate']:.2%}, "
             f"鐩堜簭姣?{row['train_payoff_ratio']:.2f}, 缁煎悎璇勫垎={score:.2f}")

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
    璁＄畻鍙傛暟缁煎悎璇勫垎锛氱泩浜忔瘮涓轰富(60%)锛岃儨鐜囦负杈?40%)

    璇勫垎鍏紡锛?
    score = 0.6 * 鏍囧噯鍖朠F + 0.4 * 鏍囧噯鍖栬儨鐜?
    """
    pf = row["train_profit_factor"]
    win_rate = row["train_win_rate"]

    # PF鏍囧噯鍖栵細鏈熸湜鑼冨洿 1.0 - 3.0锛屾槧灏勫埌 0-100
    pf_normalized = max(0, min(100, (pf - 1.0) / 2.0 * 100)) if pf != float("inf") else 100

    # 鑳滅巼鏍囧噯鍖栵細鏈熸湜鑼冨洿 30% - 70%锛屾槧灏勫埌 0-100
    win_normalized = max(0, min(100, (win_rate - 0.3) / 0.4 * 100))

    return 0.6 * pf_normalized + 0.4 * win_normalized


def _filter_trades_to_window(trades: pd.DataFrame, trade_start: pd.Timestamp, trade_end: pd.Timestamp) -> pd.DataFrame:
    if trades.empty or "entry_time" not in trades.columns:
        return trades
    entry_time = pd.to_datetime(trades["entry_time"], utc=True, errors="coerce")
    return trades[(entry_time >= trade_start) & (entry_time <= trade_end)].reset_index(drop=True)


def _run_backtest_window(
    window: EvaluationWindow,
    *,
    inst_id: str,
    params: StrategyParams,
    signal_timeframe: str,
    trend_timeframe: str,
    context: str,
) -> pd.DataFrame:
    trades = run_backtest(
        window.frame_with_warmup,
        inst_id=inst_id,
        params=params,
        signal_timeframe=signal_timeframe,
        trend_timeframe=trend_timeframe,
    )
    trades = _filter_trades_to_window(trades, window.trade_start, window.trade_end)
    return validate_backtest_result(trades, context=context)


def run_walk_forward_validation(
    frame: pd.DataFrame,
    *,
    inst_id: str,
    params: StrategyParams,
    train_window: int | None = None,
    valid_window: int | None = None,
    step: int | None = None,
    purge_bars: int = 0,
    embargo_bars: int = 0,
    params_grid: list[StrategyParams] | None = None,
    signal_timeframe: str | None = None,
    trend_timeframe: str | None = None,
) -> dict:
    signal_key, trend_key = _resolve_timeframes(frame, signal_timeframe, trend_timeframe)
    grid = params_grid or [params]
    warmup = _max_warmup_bars(grid, signal_key)
    train_window = train_window or max(warmup * 2, 2048)
    valid_window = valid_window or max(warmup, 768)
    step = step or valid_window
    purge_bars = max(0, int(purge_bars))
    embargo_bars = max(0, int(embargo_bars))
    if len(frame) < train_window + purge_bars + valid_window:
        log.warning(f"{inst_id} data is insufficient for walk-forward validation")
        return {}

    results: list[dict] = []
    cursor = 0
    while cursor + train_window + purge_bars + valid_window <= len(frame):
        train_frame = frame.iloc[cursor : cursor + train_window].reset_index(drop=True)
        validation_start_idx = cursor + train_window + purge_bars
        validation_end_idx = validation_start_idx + valid_window
        warmup_start_idx = max(0, validation_start_idx - warmup)
        valid_frame = frame.iloc[warmup_start_idx:validation_end_idx].reset_index(drop=True)
        validation_start_ts = pd.Timestamp(frame.iloc[validation_start_idx]["ts"]) if "ts" in frame.columns else None
        fold_grid = run_grid_search(
            train_frame,
            inst_id=inst_id,
            params_grid=grid,
            signal_timeframe=signal_key,
            trend_timeframe=trend_key,
        )
        try:
            frozen_params = select_best_params(fold_grid)
        except ValueError:
            cursor += step
            continue
        train_trades = run_backtest(
            train_frame,
            inst_id=inst_id,
            params=frozen_params,
            signal_timeframe=signal_key,
            trend_timeframe=trend_key,
        )
        valid_trades = run_backtest(
            valid_frame,
            inst_id=inst_id,
            params=frozen_params,
            signal_timeframe=signal_key,
            trend_timeframe=trend_key,
        )
        if validation_start_ts is not None:
            validation_end_ts = pd.Timestamp(frame.iloc[validation_end_idx - 1]["ts"]) if "ts" in frame.columns else validation_start_ts
            valid_trades = _filter_trades_to_window(valid_trades, validation_start_ts, validation_end_ts)
        try:
            train_summary = summarize_trades(validate_backtest_result(train_trades, context=f"{inst_id} walk_forward train"))
            valid_summary = summarize_trades(validate_backtest_result(valid_trades, context=f"{inst_id} walk_forward validation"))
        except ValueError:
            cursor += step + embargo_bars
            continue
        results.append(
            {
                "window_start": cursor,
                "train_start_ts": str(train_frame["ts"].iloc[0]) if "ts" in train_frame and not train_frame.empty else "",
                "train_end_ts": str(train_frame["ts"].iloc[-1]) if "ts" in train_frame and not train_frame.empty else "",
                "purge_bars": purge_bars,
                "validation_start_ts": str(validation_start_ts) if validation_start_ts is not None else "",
                "validation_end_ts": str(frame.iloc[validation_end_idx - 1]["ts"]) if "ts" in frame.columns else "",
                "embargo_bars": embargo_bars,
                "selected_params": asdict(frozen_params),
                "train_summary": train_summary,
                "valid_summary": valid_summary,
            }
        )
        cursor += step + embargo_bars

    if not results:
        return {}

    import numpy as np

    valid_pfs = [r["valid_summary"]["profit_factor"] for r in results]
    valid_returns = [r["valid_summary"]["total_return"] for r in results]
    valid_win_rates = [r["valid_summary"]["win_rate"] for r in results]
    finite_valid_pfs = [float(value) for value in valid_pfs if _finite_number(value)]
    pass_flags = [value >= 1.05 for value in finite_valid_pfs]
    return {
        "window_count": len(results),
        "valid_pf_mean": float(np.mean(valid_pfs)),
        "valid_pf_std": float(np.std(valid_pfs)),
        "valid_pf_cv": float(np.std(valid_pfs) / np.mean(valid_pfs)) if np.mean(valid_pfs) > 0 else 0,
        "valid_pf_median": float(np.median(finite_valid_pfs)) if finite_valid_pfs else 0.0,
        "valid_pf_pass_ratio": float(sum(pass_flags) / len(pass_flags)) if pass_flags else 0.0,
        "purge_bars": purge_bars,
        "embargo_bars": embargo_bars,
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


def _empty_symbol_research_row(inst_id: str, *, shared_params: bool, reason: str) -> dict:
    empty_summary = summarize_trades(pd.DataFrame())
    return {
        "symbol": inst_id,
        **_prefixed(empty_summary, "train"),
        **_prefixed(empty_summary, "valid"),
        "shared_params": bool(shared_params),
        "pass_fail": "failed",
        "fail_reasons": reason,
    }


def _failed_research_artifacts(
    *,
    dataset: str,
    symbols: list[SymbolData],
    signal_key: str,
    trend_key: str,
    shared_params: bool,
    reason: str,
    split_status: str,
) -> dict[str, pd.DataFrame | dict | StrategyParams]:
    single_results = pd.DataFrame(
        [_empty_symbol_research_row(symbol.inst_id, shared_params=shared_params, reason=reason) for symbol in symbols]
    )
    validation_results = single_results[
        ["symbol", "valid_total_return", "valid_profit_factor", "valid_total_trades", "pass_fail", "fail_reasons"]
    ].copy()
    empty_portfolio = summarize_trades(pd.DataFrame(), initial_equity=10000.0 * max(1, len(symbols)))
    empty_portfolio["status"] = "failed_no_valid_backtest"
    portfolio_results = pd.DataFrame(
        [
            {
                "portfolio_name": "shared_parameter_portfolio" if shared_params else "per_symbol_parameter_portfolio",
                "dataset": dataset,
                "signal_timeframe": signal_key,
                "trend_timeframe": trend_key,
                "symbols_included": len(symbols),
                **_prefixed(empty_portfolio, "train"),
                **_prefixed(empty_portfolio, "valid"),
                **_prefixed({**empty_portfolio, "status": "locked"}, "blind"),
                "blind_lock_status": "locked",
                "profitable_symbol_ratio": 0.0,
                "hit_27pct_symbol_ratio": 0.0,
                "pass_fail": "failed",
                "fail_reasons": reason,
            }
        ]
    )
    leverage_risk = build_leverage_risk_table(pd.DataFrame())
    cost_stress = replay_cost_stress(pd.DataFrame())
    checklist = build_acceptance_checklist(
        train_grid_results=pd.DataFrame(),
        selected=StrategyParams(),
        selected_params_available=False,
        validation_results=validation_results,
        portfolio_results=portfolio_results,
        leverage_risk=leverage_risk,
        cost_stress=cost_stress,
        walk_forward_results={},
        shared_params=shared_params,
        signal_timeframe=signal_key,
        split_status=split_status,
        blind_lock_status="locked",
    )
    return {
        "train_grid_results": pd.DataFrame(),
        "selected_params": {},
        "validation_results": validation_results,
        "single_symbol_results": single_results,
        "portfolio_results": portfolio_results,
        "leverage_risk": leverage_risk,
        "cost_stress": cost_stress,
        "walk_forward_results": {},
        "acceptance_checklist": checklist,
        "sample_trades": pd.DataFrame(),
        "blind_trades": pd.DataFrame(),
        "research_metadata": {
            "dataset": dataset,
            "split_status": split_status,
            "promotion_eligible": False,
            "fail_reasons": reason,
        },
    }


def _file_sha256(path: Path) -> str:
    if not path.exists() or not path.is_file():
        return ""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _content_sha256(frame: pd.DataFrame) -> str:
    cols = [col for col in ["ts", "open", "high", "low", "close", "volume", "is_closed"] if col in frame.columns]
    canonical = frame[cols].copy()
    if "ts" in canonical:
        canonical["ts"] = pd.to_datetime(canonical["ts"], utc=True, errors="coerce").astype("string")
    for col in ["open", "high", "low", "close", "volume"]:
        if col in canonical:
            canonical[col] = pd.to_numeric(canonical[col], errors="coerce").map(lambda value: format(float(value), ".12g") if pd.notna(value) else "")
    if "is_closed" in canonical:
        canonical["is_closed"] = canonical["is_closed"].astype("string")
    payload = canonical.to_csv(index=False, lineterminator="\n").encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def build_data_manifest(dataset: str, symbols: list[SymbolData]) -> dict:
    manifest = {
        "dataset_version": dataset,
        "symbols": {},
    }
    for symbol in sorted(symbols, key=lambda item: item.inst_id):
        frame = symbol.frame
        ts = pd.to_datetime(frame["ts"], utc=True, errors="coerce").dropna() if "ts" in frame else pd.Series(dtype="datetime64[ns, UTC]")
        manifest["symbols"][symbol.inst_id] = {
            "source_path": str(symbol.source_path),
            "file_sha256": _file_sha256(symbol.source_path),
            "content_sha256": _content_sha256(frame),
            "rows": int(len(frame)),
            "first_ts": str(ts.min()) if not ts.empty else "",
            "last_ts": str(ts.max()) if not ts.empty else "",
        }
    manifest_hash = hashlib.sha256(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    manifest["manifest_hash"] = manifest_hash
    return manifest


def _data_hash(symbols: list[SymbolData]) -> str:
    return build_data_manifest("research_dataset", symbols)["manifest_hash"]


def _legacy_data_hash(symbols: list[SymbolData]) -> str:
    digest = hashlib.sha256()
    for symbol in sorted(symbols, key=lambda item: item.inst_id):
        frame = symbol.frame
        digest.update(symbol.inst_id.encode("utf-8"))
        digest.update(str(symbol.source_path).encode("utf-8"))
        digest.update(str(len(frame)).encode("utf-8"))
        if "ts" in frame and not frame.empty:
            ts = pd.to_datetime(frame["ts"], utc=True, errors="coerce").dropna()
            if not ts.empty:
                digest.update(str(ts.min()).encode("utf-8"))
                digest.update(str(ts.max()).encode("utf-8"))
    return digest.hexdigest()


def _params_hash(params: StrategyParams | dict | None) -> str:
    payload = asdict(params) if isinstance(params, StrategyParams) else (params or {})
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _git_commit() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).resolve().parents[3],
            capture_output=True,
            text=True,
            check=True,
        )
    except Exception:
        return "unknown"
    return result.stdout.strip() or "unknown"


def _default_blind_registry_path() -> Path:
    return project_paths().output_dir / "research_registry" / "blind_registry.sqlite3"


class BlindRegistry:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else _default_blind_registry_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=5.0, isolation_level=None)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS blind_registry (
                    registry_id TEXT PRIMARY KEY,
                    dataset_content_hash TEXT NOT NULL,
                    research_config_hash TEXT NOT NULL,
                    parameter_hash TEXT NOT NULL,
                    code_commit TEXT NOT NULL,
                    status TEXT NOT NULL,
                    opened_at TEXT NOT NULL,
                    sealed_at TEXT,
                    manifest_json TEXT NOT NULL,
                    result_json TEXT
                )
                """
            )

    @staticmethod
    def registry_id(
        *,
        dataset_content_hash: str,
        research_config_hash: str,
        parameter_hash: str,
        code_commit: str,
    ) -> str:
        payload = "|".join([dataset_content_hash, research_config_hash, parameter_hash, code_commit])
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def open_once(self, manifest: dict) -> str:
        registry_id = str(manifest["registry_id"])
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            try:
                conn.execute("BEGIN IMMEDIATE")
                existing = conn.execute(
                    "SELECT status FROM blind_registry WHERE registry_id = ?",
                    (registry_id,),
                ).fetchone()
                if existing is not None:
                    raise RuntimeError(f"BLIND_ALREADY_OPENED:{registry_id}:{existing[0]}")
                conn.execute(
                    """
                    INSERT INTO blind_registry (
                        registry_id, dataset_content_hash, research_config_hash,
                        parameter_hash, code_commit, status, opened_at, manifest_json
                    ) VALUES (?, ?, ?, ?, ?, 'OPENED', ?, ?)
                    """,
                    (
                        registry_id,
                        str(manifest["dataset_hash"]),
                        str(manifest["config_hash"]),
                        str(manifest["params_hash"]),
                        str(manifest["git_commit"]),
                        now,
                        json.dumps(manifest, ensure_ascii=False, sort_keys=True),
                    ),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return registry_id

    def seal(self, registry_id: str, result: dict) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE blind_registry
                SET status = 'SEALED', sealed_at = ?, result_json = ?
                WHERE registry_id = ? AND status = 'OPENED'
                """,
                (now, json.dumps(result, ensure_ascii=False, sort_keys=True, default=str), registry_id),
            )


def _blind_access_manifest(
    *,
    dataset: str,
    symbols: list[SymbolData],
    signal_key: str,
    trend_key: str,
    selected: StrategyParams,
    validation_config: ResearchValidationConfig,
    release_token: str,
    research_version: str,
) -> dict:
    data_manifest = build_data_manifest(dataset, symbols)
    config_payload = {
        "signal_timeframe": signal_key,
        "trend_timeframe": trend_key,
        "validation_config": asdict(validation_config),
    }
    token_hash = hashlib.sha256(release_token.encode("utf-8")).hexdigest()
    config_hash = hashlib.sha256(json.dumps(config_payload, sort_keys=True).encode("utf-8")).hexdigest()
    params_hash = _params_hash(selected)
    git_commit = _git_commit()
    registry_id = BlindRegistry.registry_id(
        dataset_content_hash=str(data_manifest["manifest_hash"]),
        research_config_hash=config_hash,
        parameter_hash=params_hash,
        code_commit=git_commit,
    )
    manifest = {
        "blind_status": "unlocked",
        "registry_id": registry_id,
        "research_version": research_version,
        "dataset": dataset,
        "dataset_hash": data_manifest["manifest_hash"],
        "data_manifest": data_manifest,
        "config_hash": config_hash,
        "params_hash": params_hash,
        "release_token_hash": token_hash,
        "git_commit": git_commit,
        "first_access_time_utc": datetime.now(timezone.utc).isoformat(),
    }
    return manifest


def run_dataset_research_artifacts(
    *,
    dataset: str = DEFAULT_DATASET,
    params_grid: list[StrategyParams] | None = None,
    max_symbols: int | None = None,
    shared_params: bool = True,
    signal_timeframe: str | None = None,
    trend_timeframe: str | None = None,
    legacy_split: bool = False,
    unlock_blind: bool = False,
    blind_release_token: str | None = None,
    blind_release_token_sha256: str | None = None,
    blind_registry_path: str | Path | None = None,
    research_version: str = "v3.51-strict",
) -> dict[str, pd.DataFrame | dict | StrategyParams]:
    symbols = load_all_symbols(dataset)
    if max_symbols is not None:
        symbols = symbols[:max_symbols]
    if not symbols:
        raise ValueError("no symbols loaded for research")
    signal_key, trend_key = _resolve_timeframes(symbols[0].frame, signal_timeframe, trend_timeframe)
    validation_config = ResearchValidationConfig()
    split_status = "strict"
    try:
        splits = common_calendar_split(
            symbols,
            params_grid=params_grid,
            signal_timeframe=signal_key,
            config=validation_config,
        )
    except ValueError as exc:
        if not legacy_split:
            return _failed_research_artifacts(
                dataset=dataset,
                symbols=symbols,
                signal_key=signal_key,
                trend_key=trend_key,
                shared_params=shared_params,
                reason=f"STRICT_SPLIT_UNAVAILABLE:{exc}",
                split_status="strict_unavailable",
            )
        splits = {}
        split_status = "legacy_non_formal"

    blind_lock_status = "locked"
    blind_manifest: dict | None = None
    if unlock_blind:
        if not blind_release_token:
            return _failed_research_artifacts(
                dataset=dataset,
                symbols=symbols,
                signal_key=signal_key,
                trend_key=trend_key,
                shared_params=shared_params,
                reason="BLIND_RELEASE_TOKEN_REQUIRED",
                split_status=split_status,
            )
        token_hash = hashlib.sha256(blind_release_token.encode("utf-8")).hexdigest()
        if blind_release_token_sha256 and token_hash != blind_release_token_sha256:
            return _failed_research_artifacts(
                dataset=dataset,
                symbols=symbols,
                signal_key=signal_key,
                trend_key=trend_key,
                shared_params=shared_params,
                reason="BLIND_RELEASE_TOKEN_INVALID",
                split_status=split_status,
            )
        blind_lock_status = "unlocked"

    if shared_params:
        train_grid_results = run_shared_train_grid(
            symbols,
            params_grid=params_grid,
            signal_timeframe=signal_key,
            trend_timeframe=trend_key,
            splits=splits or None,
            validation_config=validation_config,
        )
        try:
            selected = select_shared_params(train_grid_results)
        except NoValidParameterSetError:
            selected = None
    else:
        train_grid_results = pd.DataFrame()
        selected = None

    if unlock_blind and selected is not None and blind_release_token:
        blind_manifest = _blind_access_manifest(
            dataset=dataset,
            symbols=symbols,
            signal_key=signal_key,
            trend_key=trend_key,
            selected=selected,
            validation_config=validation_config,
            release_token=blind_release_token,
            research_version=research_version,
        )
        try:
            BlindRegistry(blind_registry_path).open_once(blind_manifest)
        except RuntimeError as exc:
            return _failed_research_artifacts(
                dataset=dataset,
                symbols=symbols,
                signal_key=signal_key,
                trend_key=trend_key,
                shared_params=shared_params,
                reason=str(exc),
                split_status=split_status,
            )

    rows: list[dict] = []
    validation_rows: list[dict] = []
    train_trades_by_symbol: list[pd.DataFrame] = []
    valid_trades_by_symbol: list[pd.DataFrame] = []
    blind_trades_by_symbol: list[pd.DataFrame] = []

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
                blind_trades_by_symbol.append(pd.DataFrame())
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
            if splits and symbol_data.inst_id in splits:
                split = splits[symbol_data.inst_id]
                train_frame = split.train
                valid_window = split.validation_window
                blind_window = split.blind_window
            else:
                train_frame, valid_frame = split_train_valid(symbol_data.frame, valid_fraction=0.25)
                valid_window = EvaluationWindow(
                    frame_with_warmup=valid_frame,
                    trade_start=pd.Timestamp(valid_frame["ts"].iloc[0]) if "ts" in valid_frame and not valid_frame.empty else pd.Timestamp.min.tz_localize("UTC"),
                    trade_end=pd.Timestamp(valid_frame["ts"].iloc[-1]) if "ts" in valid_frame and not valid_frame.empty else pd.Timestamp.max.tz_localize("UTC"),
                )
                blind_window = EvaluationWindow(
                    frame_with_warmup=pd.DataFrame(),
                    trade_start=pd.Timestamp.min.tz_localize("UTC"),
                    trade_end=pd.Timestamp.max.tz_localize("UTC"),
                )
                blind_frame = pd.DataFrame()
            try:
                train_trades = validate_backtest_result(
                    run_backtest(
                        train_frame,
                        inst_id=symbol_data.inst_id,
                        params=selected,
                        signal_timeframe=signal_key,
                        trend_timeframe=trend_key,
                    ),
                    context=f"{symbol_data.inst_id} train",
                )
                valid_trades = _run_backtest_window(
                    valid_window,
                    inst_id=symbol_data.inst_id,
                    params=selected,
                    signal_timeframe=signal_key,
                    trend_timeframe=trend_key,
                    context=f"{symbol_data.inst_id} validation",
                )
                blind_trades = (
                    _run_backtest_window(
                        blind_window,
                        inst_id=symbol_data.inst_id,
                        params=selected,
                        signal_timeframe=signal_key,
                        trend_timeframe=trend_key,
                        context=f"{symbol_data.inst_id} blind",
                    )
                    if unlock_blind and not blind_window.frame_with_warmup.empty
                    else pd.DataFrame()
                )
            except ValueError as exc:
                train_summary = summarize_trades(pd.DataFrame())
                valid_summary = summarize_trades(pd.DataFrame())
                evaluation = {
                    "pass_fail": "failed",
                    "reasons": f"BACKTEST_RESULT_INVALID:{exc}",
                }
                rows.append(
                    {
                        "symbol": symbol_data.inst_id,
                        **_prefixed(train_summary, "train"),
                        **_prefixed(valid_summary, "valid"),
                        **asdict(selected),
                        "shared_params": bool(shared_params),
                        "pass_fail": evaluation["pass_fail"],
                        "fail_reasons": evaluation["reasons"],
                    }
                )
                blind_trades_by_symbol.append(pd.DataFrame())
                validation_rows.append(
                    {
                        "symbol": symbol_data.inst_id,
                        **_prefixed(valid_summary, "valid"),
                        "pass_fail": evaluation["pass_fail"],
                        "fail_reasons": evaluation["reasons"],
                    }
                )
                continue
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
            blind_trades = pd.DataFrame()

        train_trades_by_symbol.append(train_trades)
        valid_trades_by_symbol.append(valid_trades)
        blind_trades_by_symbol.append(blind_trades)
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
    try:
        train_portfolio = combine_trade_summaries(train_trades_by_symbol, symbol_count=len(symbols))
    except ValueError:
        train_portfolio = summarize_trades(pd.DataFrame(), initial_equity=10000.0 * max(1, len(symbols)))
        train_portfolio["status"] = "failed_no_valid_backtest"
    try:
        valid_portfolio = combine_trade_summaries(valid_trades_by_symbol, symbol_count=len(symbols))
    except ValueError:
        valid_portfolio = summarize_trades(pd.DataFrame(), initial_equity=10000.0 * max(1, len(symbols)))
        valid_portfolio["status"] = "failed_no_valid_backtest"
    try:
        blind_portfolio = combine_trade_summaries(blind_trades_by_symbol, symbol_count=len(symbols))
    except ValueError:
        blind_portfolio = summarize_trades(pd.DataFrame(), initial_equity=10000.0 * max(1, len(symbols)))
        blind_portfolio["status"] = "locked" if not unlock_blind else "failed_no_blind_backtest"
    walk_forward_results: dict = {}
    if shared_params and selected is not None and splits:
        walk_rows = []
        for symbol_data in symbols:
            split = splits.get(symbol_data.inst_id)
            if split is None:
                continue
            wf_frame = pd.concat([split.train, split.validation], ignore_index=True).sort_values("ts").reset_index(drop=True)
            result = run_walk_forward_validation(
                wf_frame,
                inst_id=symbol_data.inst_id,
                params=selected,
                params_grid=[selected],
                signal_timeframe=signal_key,
                trend_timeframe=trend_key,
                purge_bars=validation_config.purge_bars,
                embargo_bars=validation_config.embargo_bars,
            )
            if result:
                walk_rows.append({"symbol": symbol_data.inst_id, **{key: value for key, value in result.items() if key != "windows"}})
        if walk_rows:
            walk_table = pd.DataFrame(walk_rows)
            finite_pf = pd.to_numeric(walk_table["valid_pf_median"], errors="coerce").dropna()
            pass_ratio = pd.to_numeric(walk_table["valid_pf_pass_ratio"], errors="coerce").dropna()
            walk_forward_results = {
                "symbol_results": walk_rows,
                "symbol_count": int(len(walk_rows)),
                "window_count": int(pd.to_numeric(walk_table["window_count"], errors="coerce").fillna(0).sum()),
                "valid_pf_median": float(finite_pf.median()) if not finite_pf.empty else 0.0,
                "valid_pf_pass_ratio": float(pass_ratio.mean()) if not pass_ratio.empty else 0.0,
                "purge_bars": validation_config.purge_bars,
                "embargo_bars": validation_config.embargo_bars,
            }
    profitable_ratio = float((single_results["valid_total_return"] > 0).mean()) if not single_results.empty else 0.0
    hit27_ratio = float((single_results["valid_hit_27pct_stop"] > 0).mean()) if not single_results.empty else 0.0
    portfolio_eval = evaluate_portfolio(
        train_portfolio,
        valid_portfolio,
        profitable_symbol_ratio=profitable_ratio,
        hit_27pct_symbol_ratio=hit27_ratio,
    )
    if train_portfolio.get("status") == "failed_no_valid_backtest" or valid_portfolio.get("status") == "failed_no_valid_backtest":
        portfolio_eval = {
            "pass_fail": "failed",
            "reasons": "BACKTEST_RESULT_INVALID",
        }
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
                **_prefixed(blind_portfolio, "blind"),
                "split_status": split_status,
                "blind_lock_status": blind_lock_status,
                "profitable_symbol_ratio": profitable_ratio,
                "hit_27pct_symbol_ratio": hit27_ratio,
                "pass_fail": portfolio_eval["pass_fail"],
                "fail_reasons": portfolio_eval["reasons"],
            }
        ]
    )
    valid_trades = pd.concat([frame for frame in valid_trades_by_symbol if not frame.empty], ignore_index=True) if any(not frame.empty for frame in valid_trades_by_symbol) else pd.DataFrame()
    blind_trades = pd.concat([frame for frame in blind_trades_by_symbol if not frame.empty], ignore_index=True) if any(not frame.empty for frame in blind_trades_by_symbol) else pd.DataFrame()
    if blind_manifest:
        BlindRegistry(blind_registry_path).seal(
            str(blind_manifest["registry_id"]),
            {
                "blind_total_trades": int(len(blind_trades)),
                "blind_portfolio_status": blind_portfolio.get("status", ""),
                "blind_profit_factor": blind_portfolio.get("profit_factor", 0.0),
            },
        )
    leverage_risk = build_leverage_risk_table(valid_trades)
    cost_stress = replay_cost_stress(valid_trades, initial_equity=10000.0 * max(1, len(symbols)))
    checklist = build_acceptance_checklist(
        train_grid_results=train_grid_results,
        selected=selected if selected is not None else StrategyParams(),
        selected_params_available=selected is not None,
        validation_results=validation_results,
        portfolio_results=portfolio_results,
        leverage_risk=leverage_risk,
        cost_stress=cost_stress,
        walk_forward_results=walk_forward_results,
        shared_params=shared_params,
        signal_timeframe=signal_key,
        split_status=split_status,
        blind_lock_status=blind_lock_status,
    )
    return {
        "train_grid_results": train_grid_results,
        "selected_params": selected if selected is not None else {},
        "validation_results": validation_results,
        "single_symbol_results": single_results,
        "portfolio_results": portfolio_results,
        "leverage_risk": leverage_risk,
        "cost_stress": cost_stress,
        "walk_forward_results": walk_forward_results,
        "acceptance_checklist": checklist,
        "sample_trades": valid_trades,
        "blind_trades": blind_trades,
        "blind_access_manifest": blind_manifest or {},
        "research_metadata": {
            "dataset": dataset,
            "split_status": split_status,
            "blind_lock_status": blind_lock_status,
            "promotion_eligible": bool(not checklist.empty and checklist["passed"].astype(bool).all()),
        },
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
    selected_params_available: bool = True,
    validation_results: pd.DataFrame,
    portfolio_results: pd.DataFrame,
    leverage_risk: pd.DataFrame,
    cost_stress: pd.DataFrame | None = None,
    walk_forward_results: dict | None = None,
    shared_params: bool,
    signal_timeframe: str = DEFAULT_SIGNAL_TIMEFRAME,
    split_status: str = "strict",
    blind_lock_status: str = "locked",
) -> pd.DataFrame:
    portfolio = portfolio_results.iloc[0].to_dict() if not portfolio_results.empty else {}
    near_liq_count = int(leverage_risk["near_liq_flag"].fillna(False).sum()) if "near_liq_flag" in leverage_risk else 0
    expected_grid_size = len(parameter_grid(signal_timeframe))
    stress_scenarios = set(cost_stress["scenario"].astype(str)) if cost_stress is not None and not cost_stress.empty else set()
    stress_sources = set(cost_stress["recompute_source"].astype(str)) if cost_stress is not None and "recompute_source" in cost_stress else set()
    stable_gate = bool(
        not train_grid_results.empty
        and "finite_profit_factor" in train_grid_results
        and "stable_neighbor_count" in train_grid_results
        and "stable_neighbor_ratio" in train_grid_results
    )
    if stable_gate and "passed_train_gate" in train_grid_results:
        stable_gate = bool(train_grid_results["passed_train_gate"].fillna(False).any())
    walk_forward_results = walk_forward_results or {}
    walk_forward_pass = bool(
        walk_forward_results.get("window_count", 0) > 0
        and walk_forward_results.get("purge_bars", 0) > 0
        and walk_forward_results.get("embargo_bars", 0) > 0
        and float(walk_forward_results.get("valid_pf_pass_ratio", 0.0)) >= 0.5
    )
    checks = [
        ("exchange_okx_only", True, "OKX SWAP only"),
        ("strict_common_calendar_split", split_status == "strict", split_status),
        (
            "parameter_grid_evaluated",
            len(train_grid_results) > 0 or not shared_params,
            f"current rows {len(train_grid_results)}, full {signal_timeframe} grid {expected_grid_size}",
        ),
        ("shared_params_selected", shared_params and selected_params_available, json.dumps(asdict(selected), ensure_ascii=False)),
        ("validation_once_after_freeze", shared_params and not validation_results.empty, "validation uses frozen params"),
        (
            "finite_pf_and_neighbor_stability_gate",
            stable_gate,
            f"min_count_ratio={ResearchValidationConfig().min_neighbor_count}/{ResearchValidationConfig().min_neighbor_ratio}",
        ),
        (
            "purged_walk_forward_gate",
            walk_forward_pass,
            json.dumps(
                {
                    "windows": walk_forward_results.get("window_count", 0),
                    "median_pf": walk_forward_results.get("valid_pf_median", 0.0),
                    "pass_ratio": walk_forward_results.get("valid_pf_pass_ratio", 0.0),
                    "purge": walk_forward_results.get("purge_bars", 0),
                    "embargo": walk_forward_results.get("embargo_bars", 0),
                },
                ensure_ascii=False,
            ),
        ),
        ("blind_set_locked", blind_lock_status == "locked", blind_lock_status),
        (
            "cost_stress_replay_three_scenarios",
            {"baseline", "stress_1_5x", "stress_2x"}.issubset(stress_scenarios)
            and bool(stress_sources)
            and "legacy_cost_fallback" not in stress_sources,
            ",".join(sorted(stress_scenarios)),
        ),
        ("portfolio_valid_trades_ge_80", portfolio.get("valid_total_trades", 0) >= 80, str(portfolio.get("valid_total_trades", 0))),
        ("near_liq_zero", near_liq_count == 0, str(near_liq_count)),
        ("live_orders_disabled", True, "manual confirmation only"),
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
    sample_trades = artifacts.get("sample_trades")
    if not isinstance(sample_trades, pd.DataFrame):
        raise ValueError("research artifacts missing sample_trades")
    validate_backtest_result(sample_trades, context="research sample_trades")

    portfolio_results = artifacts.get("portfolio_results")
    if not isinstance(portfolio_results, pd.DataFrame) or portfolio_results.empty:
        raise ValueError("research artifacts missing portfolio_results")
    required_portfolio = {"valid_total_trades", "valid_total_return", "valid_profit_factor", "pass_fail"}
    missing_portfolio = sorted(required_portfolio.difference(portfolio_results.columns))
    if missing_portfolio:
        raise ValueError(f"portfolio_results missing columns: {', '.join(missing_portfolio)}")

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
        "cost_stress": out / "cost_stress.csv",
        "walk_forward_results": out / "walk_forward_results.json",
        "acceptance_checklist": out / "acceptance_checklist.csv",
        "sample_trades": out / "sample_trades.csv",
        "blind_trades": out / "blind_trades.csv",
        "blind_access_manifest": out / "blind_access_manifest.json",
        "sample_summary": out / "sample_summary.json",
        "final_report": out / "final_report.md",
        "selection_memo": out / "selection_memo.md",
    }
    for key in [
        "train_grid_results",
        "validation_results",
        "single_symbol_results",
        "portfolio_results",
        "leverage_risk",
        "cost_stress",
        "acceptance_checklist",
        "sample_trades",
        "blind_trades",
    ]:
        value = artifacts.get(key)
        if isinstance(value, pd.DataFrame):
            value.to_csv(paths[key], index=False, encoding="utf-8")
    if isinstance(artifacts.get("portfolio_results"), pd.DataFrame):
        artifacts["portfolio_results"].to_csv(paths["portfolio_result"], index=False, encoding="utf-8")
    for key in ["walk_forward_results", "blind_access_manifest"]:
        value = artifacts.get(key)
        if isinstance(value, dict):
            paths[key].write_text(json.dumps(value, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
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
            "# Parameter Freeze Memo",
            "",
            f"- Training grid rows: {rows}",
            "- Selection rule: pass finite-PF, minimum-trade, drawdown, concentration, and neighbor-stability gates before ranking.",
            "- Validation rule: frozen parameters are evaluated once on validation data; blind data stays locked unless an explicit release token is supplied.",
            f"- Frozen parameters: `{json.dumps(selected_dict, ensure_ascii=False)}`",
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
            "# OKX Signal Research Acceptance Report",
            "",
            f"- Portfolio status: {row.get('pass_fail', 'unknown')}",
            f"- Validation return: {row.get('valid_total_return', 0)}",
            f"- Validation PF: {row.get('valid_profit_factor', 0)}",
            f"- Validation payoff ratio: {row.get('valid_payoff_ratio', 0)}",
            f"- Validation trades: {row.get('valid_total_trades', 0)}",
            f"- Failure reasons: {row.get('fail_reasons', '')}",
            f"- Failed checks: {','.join(failed_checks) if failed_checks else 'none'}",
            "",
            "This report is for local research and manual review only. It does not enable automated order execution.",
        ]
    )
