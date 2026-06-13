from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, TYPE_CHECKING

import numpy as np
import pandas as pd

from okx_signal_system.backtest.runner import run_backtest, split_train_valid, summarize_trades
from okx_signal_system.config import project_paths
from okx_signal_system.features.indicators import prior_breakout_levels, resample_4h
from okx_signal_system.risk.model import Ledger, RiskConfig, smart_leverage_for_signal, validate_signal
from okx_signal_system.strategy.trend_breakout import StrategyParams, TradeSignal

if TYPE_CHECKING:
    from okx_signal_system.data.loader import SymbolData


PUSH_BLOCKING_REASONS = frozenset(
    {
        "anti_future_check_failed",
        "smart_leverage_check_failed",
        "near_liq_in_train_or_validation",
        "training_no_trades",
        "validation_no_trades",
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
        fast_ema=int(data.get("fast_ema", 20)),
        slow_ema=int(data.get("slow_ema", 60)),
        breakout_window=int(data.get("breakout_window", 40)),
        atr_stop_mult=float(data.get("atr_stop_mult", 2.0)),
        take_profit_mult=max(float(data.get("take_profit_mult", 3.5)), 3.5),
        max_hold_bars=int(data.get("max_hold_bars", 48)),
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


def _anti_future_checks() -> dict[str, bool]:
    levels = prior_breakout_levels(pd.DataFrame({"high": [10, 11, 12, 50], "low": [8, 7, 6, 1]}), 3)
    breakout_ok = bool(levels.loc[3, "breakout_high"] == 12 and levels.loc[3, "breakout_low"] == 6)

    probe = pd.DataFrame(
        {
            "ts": pd.date_range("2026-01-01", periods=7, freq="1h", tz="UTC"),
            "open": range(7),
            "high": range(1, 8),
            "low": range(7),
            "close": range(1, 8),
            "volume": [100.0] * 7,
        }
    )
    four_h = resample_4h(probe)
    incomplete_marked = bool((~four_h["complete_4h"].astype(bool)).any())

    return {
        "prior_breakout_excludes_current_bar": breakout_ok,
        "incomplete_4h_not_tradable": incomplete_marked,
    }


def _combine_summaries(trade_frames: Iterable[pd.DataFrame], *, initial_equity: float) -> dict:
    frames = [frame for frame in trade_frames if frame is not None and not frame.empty]
    if not frames:
        return summarize_trades(pd.DataFrame(), initial_equity=initial_equity)
    combined = pd.concat(frames, ignore_index=True).sort_values("exit_time").reset_index(drop=True)
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
    dataset: str = "okx_1h_extended",
    output_dir: str | Path | None = None,
    max_symbols: int | None = 6,
    history_tail: int = 2500,
) -> StartupQualityReport:
    params = load_selected_strategy_params(output_dir)
    from okx_signal_system.data.loader import load_all_symbols
    all_symbols = _select_symbols(load_all_symbols(dataset), symbols, max_symbols)

    train_trades: list[pd.DataFrame] = []
    valid_trades: list[pd.DataFrame] = []
    stale_symbols: list[str] = []
    reasons: list[str] = []

    for symbol_data in all_symbols:
        frame = symbol_data.frame.tail(history_tail).reset_index(drop=True)
        age = latest_bar_age_hours(frame)
        if age is None or age > 3.0:
            stale_symbols.append(symbol_data.inst_id)
        if len(frame) < max(params.slow_ema + params.breakout_window + 80, 240):
            reasons.append(f"{symbol_data.inst_id}:history_too_short")
            continue
        train_frame, valid_frame = split_train_valid(frame, valid_fraction=0.25)
        train_trades.append(run_backtest(train_frame, inst_id=symbol_data.inst_id, params=params))
        valid_trades.append(run_backtest(valid_frame, inst_id=symbol_data.inst_id, params=params))

    initial_equity = 10000.0 * max(1, len(all_symbols))
    train_summary = _combine_summaries(train_trades, initial_equity=initial_equity)
    valid_summary = _combine_summaries(valid_trades, initial_equity=initial_equity)
    anti_future = _anti_future_checks()
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
