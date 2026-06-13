from __future__ import annotations

import asyncio
import json
import logging
import math
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import pandas as pd

from okx_signal_system.backtest.research import run_shared_train_grid, select_shared_params
from okx_signal_system.backtest.runner import run_backtest, split_train_valid, summarize_trades
from okx_signal_system.config import load_config, project_paths
from okx_signal_system.data.loader import SymbolData, closed_bars, load_all_symbols
from okx_signal_system.ml.shadow_trading import ShadowTradingLedger
from okx_signal_system.strategy.trend_breakout import StrategyParams
from okx_signal_system.timeframe import bars_for_hours, default_trend_timeframe, timeframe_spec
from okx_signal_system.training.startup_quality import (
    _anti_future_checks,
    _json_safe,
    _min_history_bars,
    _select_symbols,
    _stress_checks,
    load_selected_strategy_params,
)

log = logging.getLogger(__name__)

PARAM_FIELDS = (
    "fast_ema",
    "slow_ema",
    "breakout_window",
    "atr_stop_mult",
    "take_profit_mult",
    "max_hold_bars",
    "atr_window",
)


@dataclass(frozen=True)
class LearningReviewConfig:
    daily_review_enabled: bool = True
    review_interval_hours: float = 24.0
    history_days: int = 365 * 3
    min_validation_trades: int = 30
    min_validation_profit_factor: float = 1.05
    min_profit_factor_delta: float = 0.05
    min_profit_factor_ratio: float = 1.05
    max_validation_drawdown: float = 0.20
    max_drawdown_worsening: float = 0.02
    max_param_distance: float = 0.35
    max_train_valid_pf_ratio: float = 3.0
    min_profitable_symbol_ratio: float = 0.35
    shadow_min_closed_signals: int = 30
    shadow_min_quality_score: float = 70.0
    max_candidate_params: int = 17
    auto_promote_params: bool = False
    live_param_updates_enabled: bool = False

    @classmethod
    def from_mapping(cls, data: dict | None) -> "LearningReviewConfig":
        raw = data or {}
        return cls(
            daily_review_enabled=bool(raw.get("daily_review_enabled", True)),
            review_interval_hours=float(raw.get("review_interval_hours", 24.0)),
            history_days=int(raw.get("history_days", 365 * 3)),
            min_validation_trades=int(raw.get("min_validation_trades", 30)),
            min_validation_profit_factor=float(raw.get("min_validation_profit_factor", 1.05)),
            min_profit_factor_delta=float(raw.get("min_profit_factor_delta", 0.05)),
            min_profit_factor_ratio=float(raw.get("min_profit_factor_ratio", 1.05)),
            max_validation_drawdown=float(raw.get("max_validation_drawdown", 0.20)),
            max_drawdown_worsening=float(raw.get("max_drawdown_worsening", 0.02)),
            max_param_distance=float(raw.get("max_param_distance", 0.35)),
            max_train_valid_pf_ratio=float(raw.get("max_train_valid_pf_ratio", 3.0)),
            min_profitable_symbol_ratio=float(raw.get("min_profitable_symbol_ratio", 0.35)),
            shadow_min_closed_signals=int(raw.get("shadow_min_closed_signals", 30)),
            shadow_min_quality_score=float(raw.get("shadow_min_quality_score", 70.0)),
            max_candidate_params=int(raw.get("max_candidate_params", 17)),
            auto_promote_params=bool(raw.get("auto_promote_params", False)),
            live_param_updates_enabled=bool(raw.get("live_param_updates_enabled", False)),
        )


@dataclass(frozen=True)
class DailyLearningReviewReport:
    generated_at: str
    next_run_at: str
    dataset: str
    signal_timeframe: str
    trend_timeframe: str
    symbols_checked: int
    closed_kline_status: dict
    frame_checks: dict
    shadow_summary: dict
    shadow_updates: int
    current_params: dict
    candidate_params: dict
    current_train_summary: dict
    current_valid_summary: dict
    candidate_train_summary: dict
    candidate_valid_summary: dict
    current_symbol_results: list[dict]
    candidate_symbol_results: list[dict]
    train_grid_meta: dict
    anti_future_checks: dict
    stress_checks: dict
    overfit_checks: dict
    candidate_gate_passed: bool
    auto_promote_enabled: bool
    promotion_allowed: bool
    reasons: list[str]


def _read_json(path: Path, fallback):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return fallback


def _parse_utc(value: str | None) -> pd.Timestamp | None:
    if not value:
        return None
    try:
        ts = pd.Timestamp(value)
    except Exception:
        return None
    if ts.tzinfo is None:
        return ts.tz_localize("UTC")
    return ts.tz_convert("UTC")


def _last_review_time(path: Path) -> pd.Timestamp | None:
    payload = _read_json(path, {})
    if isinstance(payload, dict):
        parsed = _parse_utc(str(payload.get("generated_at") or ""))
        if parsed is not None:
            return parsed
    if not path.exists():
        return None
    return pd.Timestamp(path.stat().st_mtime, unit="s", tz="UTC")


def should_run_daily_review(
    output_path: str | Path,
    *,
    interval_hours: float = 24.0,
    now: datetime | pd.Timestamp | None = None,
) -> bool:
    path = Path(output_path)
    last = _last_review_time(path)
    if last is None:
        return True
    ref = pd.Timestamp(now or datetime.now(timezone.utc))
    if ref.tzinfo is None:
        ref = ref.tz_localize("UTC")
    else:
        ref = ref.tz_convert("UTC")
    return (ref - last).total_seconds() >= max(1.0, interval_hours * 3600)


def seconds_until_next_review(
    output_path: str | Path,
    *,
    interval_hours: float = 24.0,
    now: datetime | pd.Timestamp | None = None,
) -> float:
    last = _last_review_time(Path(output_path))
    if last is None:
        return 1.0
    ref = pd.Timestamp(now or datetime.now(timezone.utc))
    if ref.tzinfo is None:
        ref = ref.tz_localize("UTC")
    else:
        ref = ref.tz_convert("UTC")
    due_at = last + pd.Timedelta(hours=max(1.0, interval_hours))
    return max(60.0, float((due_at - ref).total_seconds()))


def _summary_metric(summary: dict, key: str, default: float = 0.0) -> float:
    value = summary.get(key, default)
    if isinstance(value, str) and value.lower() == "inf":
        return math.inf
    if isinstance(value, str) and value.lower() == "-inf":
        return -math.inf
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result


def _as_comparable_pf(value: float) -> float:
    if math.isinf(value):
        return 999999.0 if value > 0 else -999999.0
    if math.isnan(value):
        return 0.0
    return value


def parameter_distance(current: StrategyParams, candidate: StrategyParams) -> float:
    distances = []
    for field in PARAM_FIELDS:
        left = float(getattr(current, field))
        right = float(getattr(candidate, field))
        distances.append(abs(right - left) / max(abs(left), 1.0))
    return float(max(distances) if distances else 0.0)


def _sanitize_params(params: StrategyParams) -> StrategyParams:
    fast = max(2, int(round(params.fast_ema)))
    slow = max(fast + 1, int(round(params.slow_ema)))
    breakout = max(3, int(round(params.breakout_window)))
    hold = max(3, int(round(params.max_hold_bars)))
    atr_window = max(5, int(round(params.atr_window)))
    return StrategyParams(
        fast_ema=fast,
        slow_ema=slow,
        breakout_window=breakout,
        atr_stop_mult=float(max(1.0, min(8.0, params.atr_stop_mult))),
        take_profit_mult=float(max(3.5, min(10.0, params.take_profit_mult))),
        max_hold_bars=hold,
        atr_window=atr_window,
    )


def _baseline_for_timeframe(timeframe: str) -> StrategyParams:
    tf = timeframe_spec(timeframe).key
    if tf == "5m":
        return StrategyParams(
            fast_ema=24,
            slow_ema=96,
            breakout_window=72,
            atr_stop_mult=2.4,
            take_profit_mult=4.0,
            max_hold_bars=144,
        )
    if tf == "15m":
        return StrategyParams(
            fast_ema=120,
            slow_ema=720,
            breakout_window=384,
            atr_stop_mult=4.0,
            take_profit_mult=6.0,
            max_hold_bars=768,
        )
    return StrategyParams()


def local_candidate_grid(
    current: StrategyParams,
    *,
    signal_timeframe: str = "15m",
    max_candidates: int = 17,
) -> list[StrategyParams]:
    candidates: list[StrategyParams] = [current, _baseline_for_timeframe(signal_timeframe)]

    for field in ("fast_ema", "slow_ema", "breakout_window", "max_hold_bars"):
        base = float(getattr(current, field))
        for mult in (0.85, 1.15):
            candidates.append(replace(current, **{field: int(round(base * mult))}))

    for value in (current.atr_stop_mult - 0.4, current.atr_stop_mult + 0.4):
        candidates.append(replace(current, atr_stop_mult=value))
    for value in (3.5, current.take_profit_mult - 0.8, current.take_profit_mult + 0.8):
        candidates.append(replace(current, take_profit_mult=value))

    for atr_mult, tp_mult in (
        (max(1.0, current.atr_stop_mult - 0.4), max(3.5, current.take_profit_mult - 0.8)),
        (current.atr_stop_mult + 0.4, current.take_profit_mult + 0.8),
    ):
        candidates.append(replace(current, atr_stop_mult=atr_mult, take_profit_mult=tp_mult))

    seen: set[tuple] = set()
    unique: list[StrategyParams] = []
    for item in candidates:
        sanitized = _sanitize_params(item)
        key = tuple(round(float(getattr(sanitized, field)), 8) for field in PARAM_FIELDS)
        if key in seen:
            continue
        seen.add(key)
        unique.append(sanitized)
        if len(unique) >= max(1, max_candidates):
            break
    return unique


def _combine_trade_summaries(
    frames: Iterable[pd.DataFrame],
    *,
    symbol_count: int,
    initial_equity_per_symbol: float = 10000.0,
) -> dict:
    usable = [frame for frame in frames if frame is not None and not frame.empty]
    initial_equity = initial_equity_per_symbol * max(1, symbol_count)
    if not usable:
        return summarize_trades(pd.DataFrame(), initial_equity=initial_equity)
    combined = pd.concat(usable, ignore_index=True)
    if "exit_time" in combined.columns:
        combined = combined.sort_values("exit_time").reset_index(drop=True)
    return summarize_trades(combined, initial_equity=initial_equity)


def _profitable_symbol_ratio(symbol_results: list[dict]) -> float:
    evaluated = [row for row in symbol_results if row.get("status") == "evaluated"]
    if not evaluated:
        return 0.0
    return float(sum(1 for row in evaluated if float(row.get("valid_total_return", 0.0)) > 0) / len(evaluated))


def _evaluate_params(
    symbols: list[SymbolData],
    *,
    params: StrategyParams,
    signal_timeframe: str,
    trend_timeframe: str,
    history_tail: int,
) -> dict:
    train_trades: list[pd.DataFrame] = []
    valid_trades: list[pd.DataFrame] = []
    rows: list[dict] = []
    order_ok = True

    min_bars = _min_history_bars(params, signal_timeframe=signal_timeframe, trend_timeframe=trend_timeframe)
    for symbol_data in symbols:
        frame = symbol_data.frame.tail(history_tail).reset_index(drop=True)
        if len(frame) < min_bars:
            rows.append(
                {
                    "symbol": symbol_data.inst_id,
                    "status": "skipped_history_too_short",
                    "rows": len(frame),
                    "min_rows": min_bars,
                }
            )
            continue

        train_frame, valid_frame = split_train_valid(frame, valid_fraction=0.25)
        if (
            train_frame.empty
            or valid_frame.empty
            or pd.to_datetime(train_frame["ts"], utc=True).max() >= pd.to_datetime(valid_frame["ts"], utc=True).min()
        ):
            order_ok = False
            rows.append({"symbol": symbol_data.inst_id, "status": "skipped_bad_train_valid_order", "rows": len(frame)})
            continue

        train = run_backtest(
            train_frame,
            inst_id=symbol_data.inst_id,
            params=params,
            signal_timeframe=signal_timeframe,
            trend_timeframe=trend_timeframe,
        )
        valid = run_backtest(
            valid_frame,
            inst_id=symbol_data.inst_id,
            params=params,
            signal_timeframe=signal_timeframe,
            trend_timeframe=trend_timeframe,
        )
        train_trades.append(train)
        valid_trades.append(valid)
        valid_summary = summarize_trades(valid)
        rows.append(
            {
                "symbol": symbol_data.inst_id,
                "status": "evaluated",
                "rows": len(frame),
                "train_rows": len(train_frame),
                "valid_rows": len(valid_frame),
                "train_total_trades": int(len(train)),
                "valid_total_trades": int(len(valid)),
                "valid_total_return": valid_summary.get("total_return", 0.0),
                "valid_profit_factor": valid_summary.get("profit_factor", 0.0),
                "valid_max_drawdown": valid_summary.get("max_drawdown", 0.0),
            }
        )

    return {
        "train_summary": _combine_trade_summaries(train_trades, symbol_count=len(symbols)),
        "valid_summary": _combine_trade_summaries(valid_trades, symbol_count=len(symbols)),
        "symbol_results": rows,
        "symbols_evaluated": sum(1 for row in rows if row.get("status") == "evaluated"),
        "train_valid_order_ok": order_ok,
    }


def evaluate_candidate_gates(
    *,
    current_train_summary: dict,
    current_valid_summary: dict,
    candidate_train_summary: dict,
    candidate_valid_summary: dict,
    current_params: StrategyParams,
    candidate_params: StrategyParams,
    anti_future_checks: dict,
    frame_checks: dict,
    shadow_summary: dict,
    candidate_symbol_results: list[dict] | None = None,
    config: LearningReviewConfig | None = None,
) -> dict:
    cfg = config or LearningReviewConfig()
    checks: dict[str, bool | float | int] = {}
    reasons: list[str] = []

    anti_future_ok = all(bool(value) for value in anti_future_checks.values())
    checks["anti_future_ok"] = anti_future_ok
    if not anti_future_ok:
        reasons.append("anti_future_check_failed")

    frame_ok = all(bool(value) for value in frame_checks.values())
    checks["frame_integrity_ok"] = frame_ok
    if not frame_ok:
        reasons.append("frame_integrity_failed")

    candidate_trades = int(_summary_metric(candidate_valid_summary, "total_trades", 0))
    checks["validation_trades"] = candidate_trades
    checks["validation_min_trades_ok"] = candidate_trades >= cfg.min_validation_trades
    if not checks["validation_min_trades_ok"]:
        reasons.append("candidate_validation_trades_too_low")

    current_pf = _summary_metric(current_valid_summary, "profit_factor", 0.0)
    candidate_pf = _summary_metric(candidate_valid_summary, "profit_factor", 0.0)
    candidate_train_pf = _summary_metric(candidate_train_summary, "profit_factor", 0.0)
    required_pf = max(
        cfg.min_validation_profit_factor,
        current_pf + cfg.min_profit_factor_delta if math.isfinite(current_pf) else math.inf,
        current_pf * cfg.min_profit_factor_ratio if math.isfinite(current_pf) else math.inf,
    )
    checks["current_validation_profit_factor"] = _as_comparable_pf(current_pf)
    checks["candidate_validation_profit_factor"] = _as_comparable_pf(candidate_pf)
    checks["required_validation_profit_factor"] = _as_comparable_pf(required_pf)
    pf_improved = candidate_pf >= required_pf
    if math.isinf(current_pf) and math.isinf(candidate_pf):
        current_ret = _summary_metric(current_valid_summary, "total_return", 0.0)
        candidate_ret = _summary_metric(candidate_valid_summary, "total_return", 0.0)
        pf_improved = candidate_ret > current_ret * (1.0 + cfg.min_profit_factor_delta)
    checks["profit_factor_improved"] = bool(pf_improved)
    if not pf_improved:
        reasons.append("candidate_profit_factor_improvement_too_small")

    valid_return = _summary_metric(candidate_valid_summary, "total_return", 0.0)
    checks["validation_return_positive"] = valid_return > 0
    if not checks["validation_return_positive"]:
        reasons.append("candidate_validation_return_not_positive")

    current_drawdown = _summary_metric(current_valid_summary, "max_drawdown", 0.0)
    candidate_drawdown = _summary_metric(candidate_valid_summary, "max_drawdown", 0.0)
    checks["validation_drawdown_ok"] = candidate_drawdown <= cfg.max_validation_drawdown
    checks["drawdown_not_worse"] = candidate_drawdown <= current_drawdown + cfg.max_drawdown_worsening
    if not checks["validation_drawdown_ok"]:
        reasons.append("candidate_validation_drawdown_too_high")
    if not checks["drawdown_not_worse"]:
        reasons.append("candidate_drawdown_worse_than_current")

    checks["near_liq_zero"] = int(_summary_metric(candidate_valid_summary, "near_liq_trades", 0)) == 0
    checks["no_27pct_stop_hit"] = int(_summary_metric(candidate_valid_summary, "hit_27pct_stop", 0)) == 0
    if not checks["near_liq_zero"]:
        reasons.append("candidate_near_liquidation_in_validation")
    if not checks["no_27pct_stop_hit"]:
        reasons.append("candidate_hit_27pct_cap")

    distance = parameter_distance(current_params, candidate_params)
    checks["param_distance"] = distance
    checks["param_distance_ok"] = distance <= cfg.max_param_distance
    if not checks["param_distance_ok"]:
        reasons.append("candidate_params_too_far_from_current")

    if candidate_train_pf > 0 and math.isfinite(candidate_train_pf) and math.isfinite(candidate_pf):
        train_valid_ratio = candidate_train_pf / max(candidate_pf, 1e-9)
    else:
        train_valid_ratio = 1.0
    checks["train_valid_pf_ratio"] = train_valid_ratio
    checks["train_valid_pf_gap_ok"] = train_valid_ratio <= cfg.max_train_valid_pf_ratio
    if not checks["train_valid_pf_gap_ok"]:
        reasons.append("candidate_train_valid_pf_gap_too_large")

    profitable_ratio = _profitable_symbol_ratio(candidate_symbol_results or [])
    checks["profitable_symbol_ratio"] = profitable_ratio
    checks["profitable_symbol_ratio_ok"] = profitable_ratio >= cfg.min_profitable_symbol_ratio
    if not checks["profitable_symbol_ratio_ok"]:
        reasons.append("candidate_profitable_symbol_ratio_too_low")

    closed = int(float(shadow_summary.get("closed", 0) or 0))
    avg_quality = float(shadow_summary.get("avg_quality_score", 0.0) or 0.0)
    checks["shadow_closed"] = closed
    checks["shadow_avg_quality_score"] = avg_quality
    if cfg.shadow_min_closed_signals <= 0:
        shadow_ok = True
    elif closed < cfg.shadow_min_closed_signals:
        shadow_ok = False
        reasons.append("shadow_signals_insufficient_for_promotion")
    else:
        shadow_ok = avg_quality >= cfg.shadow_min_quality_score
        if not shadow_ok:
            reasons.append("shadow_quality_below_threshold")
    checks["shadow_quality_ok"] = shadow_ok

    return {
        "passed": not reasons,
        "checks": checks,
        "reasons": reasons,
    }


def _closed_status(output_dir: Path) -> dict:
    payload = _read_json(output_dir / "closed_kline_backfill_status.json", {})
    return payload if isinstance(payload, dict) else {}


def _expected_latest_closed(status: dict) -> pd.Timestamp | None:
    return _parse_utc(str(status.get("expected_latest_closed") or ""))


def _prepare_symbols(
    *,
    dataset: str,
    watched: list[str] | None,
    max_symbols: int | None,
    expected_latest_closed: pd.Timestamp | None,
) -> list[SymbolData]:
    selected = _select_symbols(load_all_symbols(dataset), watched, max_symbols)
    prepared: list[SymbolData] = []
    for item in selected:
        frame = closed_bars(item.frame)
        if not frame.empty:
            frame = frame.copy()
            frame["ts"] = pd.to_datetime(frame["ts"], utc=True)
            if expected_latest_closed is not None:
                frame = frame[frame["ts"] <= expected_latest_closed]
            frame = frame.sort_values("ts").drop_duplicates("ts", keep="last").reset_index(drop=True)
        prepared.append(SymbolData(inst_id=item.inst_id, source_path=item.source_path, frame=frame))
    return prepared


def _frame_checks(symbols: list[SymbolData], *, expected_latest_closed: pd.Timestamp | None) -> dict[str, bool]:
    monotonic = True
    no_duplicates = True
    closed_only = True
    not_beyond_expected = True
    has_rows = bool(symbols)

    for item in symbols:
        frame = item.frame
        if frame.empty or "ts" not in frame.columns:
            has_rows = False
            continue
        ts = pd.to_datetime(frame["ts"], utc=True)
        monotonic = monotonic and bool(ts.is_monotonic_increasing)
        no_duplicates = no_duplicates and not bool(ts.duplicated().any())
        if "is_closed" in frame.columns:
            closed_only = closed_only and bool(frame["is_closed"].astype(bool).all())
        if expected_latest_closed is not None:
            not_beyond_expected = not_beyond_expected and bool(ts.max() <= expected_latest_closed)

    return {
        "has_rows": has_rows,
        "monotonic_ts": monotonic,
        "no_duplicate_ts": no_duplicates,
        "closed_bars_only": closed_only,
        "not_beyond_expected_closed": not_beyond_expected,
    }


def _run_candidate_search(
    symbols: list[SymbolData],
    *,
    current_params: StrategyParams,
    signal_timeframe: str,
    trend_timeframe: str,
    params_grid: list[StrategyParams] | None,
    max_candidate_params: int,
) -> tuple[StrategyParams, dict]:
    grid = params_grid or local_candidate_grid(
        current_params,
        signal_timeframe=signal_timeframe,
        max_candidates=max_candidate_params,
    )
    train_grid = run_shared_train_grid(
        symbols,
        params_grid=grid,
        signal_timeframe=signal_timeframe,
        trend_timeframe=trend_timeframe,
    )
    selected = select_shared_params(train_grid)
    meta = {
        "status": "passed",
        "candidate_count": len(grid),
        "train_grid_rows": int(len(train_grid)),
        "selected_params_source": "train_only_shared_grid",
    }
    if not train_grid.empty:
        best = train_grid.sort_values(
            ["passed_train_gate", "train_profit_factor", "train_win_rate", "train_total_trades"],
            ascending=[False, False, False, False],
        ).iloc[0]
        meta.update(
            {
                "best_train_profit_factor": best.get("train_profit_factor", 0),
                "best_train_total_return": best.get("train_total_return", 0),
                "best_train_total_trades": best.get("train_total_trades", 0),
            }
        )
    return selected, meta


def _symbols_with_enough_history(
    symbols: list[SymbolData],
    *,
    params: StrategyParams,
    signal_timeframe: str,
    trend_timeframe: str,
    history_tail: int,
) -> list[SymbolData]:
    min_bars = _min_history_bars(params, signal_timeframe=signal_timeframe, trend_timeframe=trend_timeframe)
    return [item for item in symbols if len(item.frame.tail(history_tail)) >= min_bars]


def run_daily_learning_review(
    *,
    symbols: list[str] | None = None,
    dataset: str | None = None,
    signal_timeframe: str | None = None,
    trend_timeframe: str | None = None,
    output_dir: str | Path | None = None,
    max_symbols: int | None = None,
    history_tail: int | None = None,
    params_grid: list[StrategyParams] | None = None,
    run_candidate_search: bool = True,
    config: LearningReviewConfig | None = None,
) -> DailyLearningReviewReport:
    base_config = load_config("base.yaml")
    data_cfg = base_config.get("data", {})
    learning_cfg = base_config.get("learning", {})
    cfg = config or LearningReviewConfig.from_mapping(learning_cfg if isinstance(learning_cfg, dict) else {})

    dataset = dataset or data_cfg.get("historical_dataset", "okx_15m_extended")
    signal_key = timeframe_spec(signal_timeframe or data_cfg.get("timeframe", "15m")).key
    trend_key = timeframe_spec(trend_timeframe or data_cfg.get("trend_timeframe") or default_trend_timeframe(signal_key)).key
    if history_tail is None:
        history_tail = bars_for_hours(24 * cfg.history_days, signal_key)

    out = Path(output_dir) if output_dir else project_paths().output_dir
    out.mkdir(parents=True, exist_ok=True)

    closed_status = _closed_status(out)
    expected = _expected_latest_closed(closed_status)
    review_symbols = _prepare_symbols(
        dataset=dataset,
        watched=symbols,
        max_symbols=max_symbols,
        expected_latest_closed=expected,
    )

    shadow = ShadowTradingLedger(out / "shadow_signals.json")
    shadow_updates = 0
    for symbol_data in review_symbols:
        shadow_updates += shadow.update_symbol(symbol_data.inst_id, symbol_data.frame)
    shadow_summary = shadow.summary()

    current_params = load_selected_strategy_params(out)
    current_eval = _evaluate_params(
        review_symbols,
        params=current_params,
        signal_timeframe=signal_key,
        trend_timeframe=trend_key,
        history_tail=history_tail,
    )

    candidate_params = current_params
    train_grid_meta = {
        "status": "skipped",
        "reason": "candidate_search_disabled",
        "candidate_count": 0,
        "selected_params_source": "current_params",
    }
    search_reasons: list[str] = []
    candidate_search_symbols = _symbols_with_enough_history(
        review_symbols,
        params=current_params,
        signal_timeframe=signal_key,
        trend_timeframe=trend_key,
        history_tail=history_tail,
    )
    if run_candidate_search and current_eval["symbols_evaluated"] > 0 and candidate_search_symbols:
        try:
            candidate_params, train_grid_meta = _run_candidate_search(
                candidate_search_symbols,
                current_params=current_params,
                signal_timeframe=signal_key,
                trend_timeframe=trend_key,
                params_grid=params_grid,
                max_candidate_params=cfg.max_candidate_params,
            )
        except Exception as exc:
            train_grid_meta = {
                "status": "failed",
                "reason": str(exc),
                "candidate_count": 0,
                "selected_params_source": "current_params",
            }
            search_reasons.append("candidate_search_failed")
    elif run_candidate_search:
        train_grid_meta = {
            "status": "skipped",
            "reason": "insufficient_history_for_candidate_search",
            "candidate_count": 0,
            "selected_params_source": "current_params",
        }

    candidate_eval = _evaluate_params(
        review_symbols,
        params=candidate_params,
        signal_timeframe=signal_key,
        trend_timeframe=trend_key,
        history_tail=history_tail,
    )

    anti_future = _anti_future_checks(signal_timeframe=signal_key, trend_timeframe=trend_key)
    stress = _stress_checks(current_params)
    frames_ok = _frame_checks(review_symbols, expected_latest_closed=expected)
    frames_ok["current_train_valid_order_ok"] = bool(current_eval.get("train_valid_order_ok"))
    frames_ok["candidate_train_valid_order_ok"] = bool(candidate_eval.get("train_valid_order_ok"))

    gate = evaluate_candidate_gates(
        current_train_summary=current_eval["train_summary"],
        current_valid_summary=current_eval["valid_summary"],
        candidate_train_summary=candidate_eval["train_summary"],
        candidate_valid_summary=candidate_eval["valid_summary"],
        current_params=current_params,
        candidate_params=candidate_params,
        anti_future_checks=anti_future,
        frame_checks=frames_ok,
        shadow_summary=shadow_summary,
        candidate_symbol_results=candidate_eval["symbol_results"],
        config=cfg,
    )

    auto_promote_enabled = bool(cfg.auto_promote_params and cfg.live_param_updates_enabled)
    promotion_allowed = bool(gate["passed"] and auto_promote_enabled)
    reasons = [*search_reasons, *gate["reasons"]]
    if gate["passed"] and not auto_promote_enabled:
        reasons.append("auto_promotion_disabled")

    now = datetime.now(timezone.utc)
    report = DailyLearningReviewReport(
        generated_at=now.isoformat(),
        next_run_at=(pd.Timestamp(now) + pd.Timedelta(hours=cfg.review_interval_hours)).isoformat(),
        dataset=dataset,
        signal_timeframe=signal_key,
        trend_timeframe=trend_key,
        symbols_checked=len(review_symbols),
        closed_kline_status={
            "all_complete": bool(closed_status.get("all_complete", False)),
            "expected_latest_closed": closed_status.get("expected_latest_closed", ""),
            "symbols_checked": int(closed_status.get("symbols_checked", 0) or 0),
        },
        frame_checks=frames_ok,
        shadow_summary=shadow_summary,
        shadow_updates=shadow_updates,
        current_params=asdict(current_params),
        candidate_params=asdict(candidate_params),
        current_train_summary=current_eval["train_summary"],
        current_valid_summary=current_eval["valid_summary"],
        candidate_train_summary=candidate_eval["train_summary"],
        candidate_valid_summary=candidate_eval["valid_summary"],
        current_symbol_results=current_eval["symbol_results"],
        candidate_symbol_results=candidate_eval["symbol_results"],
        train_grid_meta=train_grid_meta,
        anti_future_checks=anti_future,
        stress_checks=stress,
        overfit_checks=gate["checks"],
        candidate_gate_passed=bool(gate["passed"]),
        auto_promote_enabled=auto_promote_enabled,
        promotion_allowed=promotion_allowed,
        reasons=reasons,
    )

    serializable = _json_safe(asdict(report))
    (out / "daily_learning_review.json").write_text(
        json.dumps(serializable, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (out / "candidate_params.json").write_text(
        json.dumps(
            _json_safe(
                {
                    "generated_at": report.generated_at,
                    "dataset": report.dataset,
                    "signal_timeframe": report.signal_timeframe,
                    "trend_timeframe": report.trend_timeframe,
                    "candidate_params": report.candidate_params,
                    "candidate_gate_passed": report.candidate_gate_passed,
                    "auto_promote_enabled": report.auto_promote_enabled,
                    "promotion_allowed": report.promotion_allowed,
                    "reasons": report.reasons,
                    "overfit_checks": report.overfit_checks,
                }
            ),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    log.info(
        "daily learning review complete: gate=%s promotion=%s symbols=%s",
        report.candidate_gate_passed,
        report.promotion_allowed,
        report.symbols_checked,
    )
    return report


class DailyLearningReviewService:
    def __init__(
        self,
        symbols: list[str],
        *,
        dataset: str,
        signal_timeframe: str,
        trend_timeframe: str,
        output_dir: Path,
        config: LearningReviewConfig,
    ) -> None:
        self.symbols = symbols
        self.dataset = dataset
        self.signal_timeframe = signal_timeframe
        self.trend_timeframe = trend_timeframe
        self.output_dir = output_dir
        self.config = config
        self.output_path = output_dir / "daily_learning_review.json"

    def should_run(self) -> bool:
        return should_run_daily_review(
            self.output_path,
            interval_hours=self.config.review_interval_hours,
        )

    def run_once(self) -> DailyLearningReviewReport:
        return run_daily_learning_review(
            symbols=self.symbols,
            dataset=self.dataset,
            signal_timeframe=self.signal_timeframe,
            trend_timeframe=self.trend_timeframe,
            output_dir=self.output_dir,
            config=self.config,
        )

    async def run_forever(self) -> None:
        while True:
            if self.should_run():
                await asyncio.to_thread(self.run_once)
            delay = seconds_until_next_review(
                self.output_path,
                interval_hours=self.config.review_interval_hours,
            )
            await asyncio.sleep(min(delay, 3600.0))


async def run_daily_learning_review_service(symbols: list[str]) -> None:
    cfg = load_config("base.yaml")
    data_cfg = cfg.get("data", {})
    learning_cfg = cfg.get("learning", {})
    review_cfg = LearningReviewConfig.from_mapping(learning_cfg if isinstance(learning_cfg, dict) else {})
    if not review_cfg.daily_review_enabled:
        log.info("daily learning review disabled")
        return

    service = DailyLearningReviewService(
        symbols=symbols,
        dataset=data_cfg.get("historical_dataset", "okx_15m_extended"),
        signal_timeframe=timeframe_spec(data_cfg.get("timeframe", "15m")).key,
        trend_timeframe=timeframe_spec(data_cfg.get("trend_timeframe") or default_trend_timeframe(data_cfg.get("timeframe", "15m"))).key,
        output_dir=project_paths().output_dir,
        config=review_cfg,
    )
    try:
        await service.run_forever()
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        log.error("daily learning review service stopped: %s", exc)
