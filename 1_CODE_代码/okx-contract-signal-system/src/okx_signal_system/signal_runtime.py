from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from okx_signal_system.data.closed_backfill import latest_closed_candle_start, seconds_until_next_closed_run
from okx_signal_system.strategy.trend_breakout import StrategyParams, TradeSignal, build_signal
from okx_signal_system.timeframe import timeframe_spec

DEFAULT_MAX_SIGNAL_LAG_MINUTES = 20.0


def strategy_version() -> str:
    try:
        from okx_signal_system import __version__

        return str(__version__)
    except Exception:
        return "unknown"


def parameter_hash(params: StrategyParams) -> str:
    payload = json.dumps(asdict(params), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def make_signal_id(
    inst_id: str,
    candle_time: Any,
    side: str,
    strategy_version_value: str,
    parameter_hash_value: str,
) -> str:
    candle_text = pd.Timestamp(candle_time).isoformat()
    raw = f"{inst_id}|{candle_text}|{side}|{strategy_version_value}|{parameter_hash_value}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def signal_id(signal: TradeSignal, params: StrategyParams) -> str:
    return make_signal_id(
        signal.inst_id,
        signal.ts,
        signal.side,
        strategy_version(),
        parameter_hash(params),
    )


def latest_closed_signal(
    features: pd.DataFrame,
    *,
    inst_id: str,
    params: StrategyParams,
) -> TradeSignal | None:
    if features.empty:
        return None
    latest_idx = len(features) - 1
    latest_row = features.iloc[latest_idx]
    signal = build_signal(latest_row, inst_id=inst_id, params=params, frame=features, idx=latest_idx)
    if not signal.accepted:
        return None
    if pd.Timestamp(signal.ts) != pd.Timestamp(latest_row["ts"]):
        return None
    return signal


def closed_bar_lag_minutes(
    candle_time: Any,
    *,
    timeframe: str = "15m",
    now: datetime | pd.Timestamp | None = None,
) -> float:
    current = pd.Timestamp(now or datetime.now(timezone.utc))
    if current.tzinfo is None:
        current = current.tz_localize("UTC")
    else:
        current = current.tz_convert("UTC")
    candle = pd.Timestamp(candle_time)
    if candle.tzinfo is None:
        candle = candle.tz_localize("UTC")
    else:
        candle = candle.tz_convert("UTC")
    close_time = candle + pd.Timedelta(minutes=timeframe_spec(timeframe).minutes)
    return float((current - close_time).total_seconds() / 60.0)


def signal_is_stale(
    candle_time: Any,
    *,
    timeframe: str = "15m",
    now: datetime | pd.Timestamp | None = None,
    max_lag_minutes: float = DEFAULT_MAX_SIGNAL_LAG_MINUTES,
) -> bool:
    return closed_bar_lag_minutes(candle_time, timeframe=timeframe, now=now) > max_lag_minutes


def signal_is_latest_expected_closed(
    candle_time: Any,
    timeframe: str,
    *,
    now: datetime | pd.Timestamp | None = None,
    settle_seconds: int = 60,
) -> bool:
    expected = pd.Timestamp(latest_closed_candle_start(timeframe, now=now, settle_seconds=settle_seconds))
    candle = pd.Timestamp(candle_time)
    if candle.tzinfo is None:
        candle = candle.tz_localize("UTC")
    else:
        candle = candle.tz_convert("UTC")
    return candle == expected


def seconds_until_next_signal_scan(
    timeframe: str,
    *,
    now: datetime | pd.Timestamp | None = None,
    settle_seconds: int = 60,
) -> float:
    return seconds_until_next_closed_run(timeframe, now=now, settle_seconds=settle_seconds)
