from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, TYPE_CHECKING

import numpy as np
import pandas as pd

from okx_signal_system.backtest.runner import run_backtest, split_train_valid, summarize_trades, validate_backtest_result
from okx_signal_system.config import load_config, project_paths
from okx_signal_system.features.indicators import prior_breakout_levels, resample_trend
from okx_signal_system.risk.model import Ledger, RiskConfig, smart_leverage_for_signal, validate_signal
from okx_signal_system.strategy.trend_breakout import StrategyParams, TradeSignal
from okx_signal_system.strategy.vote_gate import min_vote_approval_rate
from okx_signal_system.timeframe import bars_for_hours, default_trend_timeframe, ratio_bars, timeframe_spec

if TYPE_CHECKING:
    from okx_signal_system.data.loader import SymbolData


PUSH_BLOCKING_REASONS = frozenset(
    {
        "anti_future_check_failed",
        "smart_leverage_check_failed",
        "near_liq_in_train_or_validation",
        "training_no_trades",
        "training_no_valid_backtest",
        "validation_no_trades",
        "validation_no_valid_backtest",
        "validation_return_not_positive",
        "validation_profit_factor_below_1",
    }
)


def is_push_blocking_reason(reason: str) -> bool:
    return reason in PUSH_BLOCKING_REASONS or reason.endswith(":history_too_short")


def push_blocking_reasons(reasons: Iterable[str]) -> list[str]:
    return [reason for reason in reasons if is_push_blocking_reason(reason)]


@dataclass(frozen=True)
class StartupQualityReport:
    generated_at: str
    status: str
    dataset: str
    signal_timeframe: str
    trend_timeframe: str
    selected_params: dict
    symbols_checked: int
    train_summary: dict
    valid_summary: dict
    anti_future_checks: dict
    stress_checks: dict
    stale_symbols: list[str]
    reasons: list[str]
    push_allowed: bool
    push_blocking_reasons: list[str]

    @property
    def strategy_params(self) -> StrategyParams:
        return params_from_dict(self.selected_params)


def params_from_dict(data: dict) -> StrategyParams:
    return StrategyParams(
        fast_ema=int(data.get("fast_ema", 120)),
        slow_ema=int(data.get("slow_ema", 720)),
        breakout_window=int(data.get("breakout_window", 384)),
        atr_stop_mult=float(data.get("atr_stop_mult", 4.0)),
        take_profit_mult=max(float(data.get("take_profit_mult", 6.0)), 3.5),
        max_hold_bars=int(data.get("max_hold_bars", 768)),
        atr_window=int(data.get("atr_window", 14)),
    )


def load_selected_strategy_params(output_dir: str | Path | None = None) -> StrategyParams:
    out = Path(output_dir) if output_dir else project_paths().output_dir
    path = out / "selected_params.json"
    if not path.exists():
        return StrategyParams()
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return StrategyParams()
    return params_from_dict(data)


def latest_bar_age_hours(frame: pd.DataFrame, now: pd.Timestamp | None = None) -> float | None:
    if frame.empty or "ts" not in frame.columns:
        return None
    latest = pd.to_datetime(frame["ts"].iloc[-1], utc=True)
    ref = now or pd.Timestamp.now(tz="UTC")
    return float((ref - latest).total_seconds() / 3600)


def is_latest_bar_fresh(
    frame: pd.DataFrame,
    *,
    max_lag_hours: float = 3.0,
    now: pd.Timestamp | None = None,
) -> bool:
    age = latest_bar_age_hours(frame, now)
    return age is not None and age <= max_lag_hours


def _min_history_bars(params: StrategyParams, *, signal_timeframe: str, trend_timeframe: str | None) -> int:
    trend_key = trend_timeframe or default_trend_timeframe(signal_timeframe)
    trend_ratio = ratio_bars(trend_key, signal_timeframe)
    return max(
        params.slow_ema + params.breakout_window + 80,
        params.slow_ema * trend_ratio + 100,
        240,
    )


def _anti_future_checks(*, signal_timeframe: str = "15m", trend_timeframe: str | None = None) -> dict[str, bool]:
    levels = prior_breakout_levels(pd.DataFrame({"high": [10, 11, 12, 50], "low": [8, 7, 6, 1]}), 3)
    breakout_ok = bool(levels.loc[3, "breakout_high"] == 12 and levels.loc[3, "breakout_low"] == 6)

    signal_spec = timeframe_spec(signal_timeframe)
    trend_key = trend_timeframe or default_trend_timeframe(signal_spec.key)
    trend_count = ratio_bars(trend_key, signal_spec.key)
    incomplete_marked = True
    trend_resample_excludes_right_edge = True
    if trend_count > 1:
        periods = trend_count + 3
        probe = pd.DataFrame(
            {
                "ts": pd.date_range("2026-01-01", periods=periods, freq=signal_spec.pandas_freq, tz="UTC"),
                "open": range(periods),
                "high": range(1, periods + 1),
                "low": range(periods),
                "close": range(1, periods + 1),
                "volume": [100.0] * periods,
            }
        )
        trend_frame = resample_trend(probe, signal_timeframe=signal_spec.key, trend_timeframe=trend_key)
        incomplete_marked = bool((~trend_frame["complete_trend"].astype(bool)).any())
        first_complete = trend_frame[trend_frame["complete_trend"].astype(bool)].head(1)
        if not first_complete.empty:
            trend_resample_excludes_right_edge = bool(first_complete.iloc[0]["close"] == trend_count)

    return {
        "prior_breakout_excludes_current_bar": breakout_ok,
        "incomplete_trend_not_tradable": incomplete_marked,
        "trend_resample_excludes_right_edge": trend_resample_excludes_right_edge,
    }


def _combine_summaries(trade_frames: Iterable[pd.DataFrame], *, initial_equity: float) -> dict:
    frames = [frame for frame in trade_frames if frame is not None and not frame.empty]
    if not frames:
        raise ValueError("no valid backtest rows for startup quality summary")
    combined = pd.concat(frames, ignore_index=True).sort_values("exit_time").reset_index(drop=True)
    validate_backtest_result(combined, context="startup_quality_summary")
    return summarize_trades(combined, initial_equity=initial_equity)


def _stress_checks(params: StrategyParams) -> dict[str, bool | float]:
    low = TradeSignal(
        ts=pd.Timestamp("2026-01-01T00:00:00Z"),
        inst_id="TEST-USDT-SWAP",
        side="long",
        entry_ref=100.0,
        stop_loss=99.2,
        take_profit=102.8,
        max_hold_bars=params.max_hold_bars,
        reason_codes=("TEST",),
        signal_score=5.5,
        risk_reward_ratio=3.5,
    )
    high = TradeSignal(
        ts=low.ts,
        inst_id=low.inst_id,
        side="long",
        entry_ref=100.0,
        stop_loss=99.2,
        take_profit=102.8,
        max_hold_bars=params.max_hold_bars,
        reason_codes=("TEST",),
        signal_score=9.4,
        risk_reward_ratio=3.5,
    )
    ledger = Ledger("TEST-USDT-SWAP", init_capital=10000, equity=10000)
    cfg = RiskConfig(max_leverage=10)
    low_lev = smart_leverage_for_signal(low, ledger, cfg)
    high_lev = smart_leverage_for_signal(high, ledger, cfg)
    high_decision = validate_signal(high, ledger, cfg)

    return {
        "smart_leverage_uses_signal_score": bool(high_lev > low_lev and high_lev <= 10),
        "risk_rejects_near_liquidation": bool(high_decision.accepted and not high_decision.near_liq_flag),
        "min_reward_to_risk": float(cfg.min_reward_to_risk),
        "target_reward_to_risk": float(params.take_profit_mult),
        "margin_loss_cap_pct": float(cfg.single_position_loss_pct),
        "high_score_margin_loss_pct": float(high_decision.margin_loss_pct or 0.0),
        "low_score_leverage": float(low_lev),
        "high_score_leverage": float(high_lev),
    }


def _select_symbols(symbols: list["SymbolData"], watched: list[str] | None, max_symbols: int | None) -> list["SymbolData"]:
    if watched:
        by_inst_id = {item.inst_id.upper(): item for item in symbols}
        selected = [by_inst_id[item.upper()] for item in watched if item.upper() in by_inst_id]
        if selected:
            symbols = selected
    if max_symbols is not None:
        symbols = symbols[:max_symbols]
    return symbols


def run_startup_quality_gate(
    *,
    symbols: list[str] | None = None,
    dataset: str | None = None,
    signal_timeframe: str | None = None,
    trend_timeframe: str | None = None,
    output_dir: str | Path | None = None,
    max_symbols: int | None = None,
    history_tail: int | None = None,
) -> StartupQualityReport:
    config = load_config("base.yaml")
    data_cfg = config.get("data", {})
    dataset = dataset or data_cfg.get("historical_dataset", "okx_15m_extended")
    signal_timeframe = timeframe_spec(signal_timeframe or data_cfg.get("timeframe", "15m")).key
    trend_timeframe = trend_timeframe or data_cfg.get("trend_timeframe") or default_trend_timeframe(signal_timeframe)
    trend_timeframe = timeframe_spec(trend_timeframe).key
    if history_tail is None:
        history_tail = bars_for_hours(24 * 365 * 3, signal_timeframe)
    params = load_selected_strategy_params(output_dir)
    min_vote_rate = min_vote_approval_rate(config)
    from okx_signal_system.data.loader import load_all_symbols
    all_symbols = _select_symbols(load_all_symbols(dataset), symbols, max_symbols)

    train_trades: list[pd.DataFrame] = []
    valid_trades: list[pd.DataFrame] = []
    stale_symbols: list[str] = []
    reasons: list[str] = []

    for symbol_data in all_symbols:
        frame = symbol_data.frame.tail(history_tail).reset_index(drop=True)
        age = latest_bar_age_hours(frame)
        if age is None or age > timeframe_spec(signal_timeframe).fresh_lag_hours:
            stale_symbols.append(symbol_data.inst_id)
        if len(frame) < _min_history_bars(params, signal_timeframe=signal_timeframe, trend_timeframe=trend_timeframe):
            reasons.append(f"{symbol_data.inst_id}:history_too_short")
            continue
        train_frame, valid_frame = split_train_valid(frame, valid_fraction=0.25)
        try:
            train = validate_backtest_result(
                run_backtest(
                    train_frame,
                    inst_id=symbol_data.inst_id,
                    params=params,
                    signal_timeframe=signal_timeframe,
                    trend_timeframe=trend_timeframe,
                    min_vote_approval_rate=min_vote_rate,
                ),
                context=f"{symbol_data.inst_id} train",
            )
            valid = validate_backtest_result(
                run_backtest(
                    valid_frame,
                    inst_id=symbol_data.inst_id,
                    params=params,
                    signal_timeframe=signal_timeframe,
                    trend_timeframe=trend_timeframe,
                    min_vote_approval_rate=min_vote_rate,
                ),
                context=f"{symbol_data.inst_id} validation",
            )
        except ValueError as exc:
            reasons.append(f"{symbol_data.inst_id}:invalid_backtest_result")
            log.warning("Startup quality skipped %s: %s", symbol_data.inst_id, exc)
            continue
        train_trades.append(train)
        valid_trades.append(valid)

    initial_equity = 10000.0 * max(1, len(all_symbols))
    try:
        train_summary = _combine_summaries(train_trades, initial_equity=initial_equity)
    except ValueError:
        train_summary = summarize_trades(pd.DataFrame(), initial_equity=initial_equity)
        train_summary["status"] = "failed_no_valid_backtest"
        reasons.append("training_no_valid_backtest")
    try:
        valid_summary = _combine_summaries(valid_trades, initial_equity=initial_equity)
    except ValueError:
        valid_summary = summarize_trades(pd.DataFrame(), initial_equity=initial_equity)
        valid_summary["status"] = "failed_no_valid_backtest"
        reasons.append("validation_no_valid_backtest")
    anti_future = _anti_future_checks(signal_timeframe=signal_timeframe, trend_timeframe=trend_timeframe)
    stress = _stress_checks(params)

    if not all(anti_future.values()):
        reasons.append("anti_future_check_failed")
    if not bool(stress.get("smart_leverage_uses_signal_score")):
        reasons.append("smart_leverage_check_failed")
    if train_summary.get("near_liq_trades", 0) > 0 or valid_summary.get("near_liq_trades", 0) > 0:
        reasons.append("near_liq_in_train_or_validation")
    if train_summary.get("total_trades", 0) <= 0:
        reasons.append("training_no_trades")
    if train_summary.get("total_return", 0) <= 0:
        reasons.append("training_return_not_positive")
    if train_summary.get("profit_factor", 0) < 1.0:
        reasons.append("training_profit_factor_below_1")
    if valid_summary.get("total_trades", 0) <= 0:
        reasons.append("validation_no_trades")
    if valid_summary.get("total_return", 0) <= 0:
        reasons.append("validation_return_not_positive")
    if valid_summary.get("profit_factor", 0) < 1.0:
        reasons.append("validation_profit_factor_below_1")
    if train_summary.get("profit_factor", 0) < 1.0 <= valid_summary.get("profit_factor", 0):
        reasons.append("validation_edge_not_confirmed_by_training")

    blocking_reasons = push_blocking_reasons(reasons)
    status = "passed" if not reasons else "warning"
    report = StartupQualityReport(
        generated_at=datetime.now(timezone.utc).isoformat(),
        status=status,
        dataset=dataset,
        signal_timeframe=signal_timeframe,
        trend_timeframe=trend_timeframe,
        selected_params=asdict(params),
        symbols_checked=len(all_symbols),
        train_summary=train_summary,
        valid_summary=valid_summary,
        anti_future_checks=anti_future,
        stress_checks=stress,
        stale_symbols=stale_symbols,
        reasons=reasons,
        push_allowed=not blocking_reasons,
        push_blocking_reasons=blocking_reasons,
    )

    out = Path(output_dir) if output_dir else project_paths().output_dir
    out.mkdir(parents=True, exist_ok=True)
    serializable = asdict(report)
    serializable["train_summary"] = _json_safe(serializable["train_summary"])
    serializable["valid_summary"] = _json_safe(serializable["valid_summary"])
    serializable["stress_checks"] = _json_safe(serializable["stress_checks"])
    (out / "startup_quality_gate.json").write_text(
        json.dumps(serializable, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return report


def _json_safe(value):
    if isinstance(value, dict):
        return {key: _json_safe(val) for key, val in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, float) and not np.isfinite(value):
        return "inf" if value > 0 else "-inf"
    if isinstance(value, (np.floating, np.integer)):
        return _json_safe(value.item())
    return value
