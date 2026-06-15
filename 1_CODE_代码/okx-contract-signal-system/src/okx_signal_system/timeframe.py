from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TimeframeSpec:
    key: str
    minutes: int
    pandas_freq: str
    okx_bar: str
    ws_channel: str
    file_suffix: str
    fresh_lag_hours: float

    @property
    def hours(self) -> float:
        return self.minutes / 60.0


SUPPORTED_TIMEFRAMES: dict[str, TimeframeSpec] = {
    "5m": TimeframeSpec(
        key="5m",
        minutes=5,
        pandas_freq="5min",
        okx_bar="5m",
        ws_channel="candle5m",
        file_suffix="5m",
        fresh_lag_hours=0.50,
    ),
    "15m": TimeframeSpec(
        key="15m",
        minutes=15,
        pandas_freq="15min",
        okx_bar="15m",
        ws_channel="candle15m",
        file_suffix="15m",
        fresh_lag_hours=0.35,
    ),
    "1h": TimeframeSpec(
        key="1h",
        minutes=60,
        pandas_freq="1h",
        okx_bar="1H",
        ws_channel="candle1H",
        file_suffix="1h",
        fresh_lag_hours=3.00,
    ),
    "4h": TimeframeSpec(
        key="4h",
        minutes=240,
        pandas_freq="4h",
        okx_bar="4H",
        ws_channel="candle4H",
        file_suffix="4h",
        fresh_lag_hours=6.00,
    ),
}


def normalize_timeframe(value: str | None, default: str = "1h") -> str:
    key = (value or default).strip().lower()
    aliases = {
        "5min": "5m",
        "5mins": "5m",
        "5minute": "5m",
        "15min": "15m",
        "15mins": "15m",
        "15minute": "15m",
        "60m": "1h",
        "1hour": "1h",
        "1hr": "1h",
        "240m": "4h",
        "4hour": "4h",
        "4hr": "4h",
    }
    key = aliases.get(key, key)
    if key not in SUPPORTED_TIMEFRAMES:
        raise ValueError(f"unsupported timeframe: {value}")
    return key


def timeframe_spec(value: str | None, default: str = "1h") -> TimeframeSpec:
    return SUPPORTED_TIMEFRAMES[normalize_timeframe(value, default=default)]


def default_trend_timeframe(signal_timeframe: str | None) -> str:
    signal = timeframe_spec(signal_timeframe)
    if signal.minutes < 60:
        return "1h"
    return "4h"


def ratio_bars(higher_timeframe: str | None, lower_timeframe: str | None) -> int:
    higher = timeframe_spec(higher_timeframe)
    lower = timeframe_spec(lower_timeframe)
    if higher.minutes < lower.minutes or higher.minutes % lower.minutes != 0:
        raise ValueError(f"{higher.key} must be an integer multiple of {lower.key}")
    return max(1, higher.minutes // lower.minutes)


def bars_for_hours(hours: float, timeframe: str | None) -> int:
    spec = timeframe_spec(timeframe)
    return max(1, int(round(hours * 60 / spec.minutes)))
