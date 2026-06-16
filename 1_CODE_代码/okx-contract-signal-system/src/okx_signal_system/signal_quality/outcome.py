from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

import numpy as np
import pandas as pd

LabelOutcome = Literal["TP", "SL", "TIMEOUT"]
ExitReason = Literal["take_profit", "stop_loss", "max_hold", "trend_reverse"]


@dataclass(frozen=True)
class SignalOutcomeLevels:
    entry_price: float
    stop_loss: float
    take_profit: float
    stop_dist: float
    reward_to_risk: float


@dataclass(frozen=True)
class SignalOutcomeResult:
    outcome: LabelOutcome
    exit_reason: ExitReason
    entry_idx: int
    exit_idx: int
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    entry_price: float
    exit_price: float
    stop_loss: float
    take_profit: float
    stop_dist: float
    mae: float
    mfe: float
    holding_bars: int


class SignalOutcomeSimulator:
    """Single source of truth for signal entry anchoring and TP/SL outcome scans."""

    def levels_from_signal(self, signal: Any, *, entry_price: float | None = None) -> SignalOutcomeLevels | None:
        if getattr(signal, "entry_ref", None) is None or getattr(signal, "stop_loss", None) is None or getattr(signal, "take_profit", None) is None:
            return None
        side = str(getattr(signal, "side", ""))
        if side not in {"long", "short"}:
            return None
        try:
            entry_ref = float(getattr(signal, "entry_ref"))
            source_stop = float(getattr(signal, "stop_loss"))
            source_target = float(getattr(signal, "take_profit"))
            actual_entry = float(entry_ref if entry_price is None else entry_price)
        except (TypeError, ValueError):
            return None
        stop_dist = abs(entry_ref - source_stop)
        if not all(np.isfinite(value) for value in [entry_ref, source_stop, source_target, actual_entry, stop_dist]) or stop_dist <= 0:
            return None
        reward_to_risk = abs(source_target - entry_ref) / stop_dist
        if side == "long":
            stop_loss = actual_entry - stop_dist
            take_profit = actual_entry + stop_dist * reward_to_risk
        else:
            stop_loss = actual_entry + stop_dist
            take_profit = actual_entry - stop_dist * reward_to_risk
        return SignalOutcomeLevels(
            entry_price=float(actual_entry),
            stop_loss=float(stop_loss),
            take_profit=float(take_profit),
            stop_dist=float(stop_dist),
            reward_to_risk=float(reward_to_risk),
        )

    def simulate_signal(
        self,
        signal: Any,
        frame: pd.DataFrame,
        *,
        start_idx: int | None = None,
        entry_price: float | None = None,
        closed_only: bool = True,
        after_signal_time: bool = True,
        include_entry_bar: bool = True,
        include_trend_reverse: bool = False,
        require_complete_timeout: bool = False,
    ) -> SignalOutcomeResult | None:
        if not bool(getattr(signal, "accepted", True)):
            return None
        max_hold_bars = getattr(signal, "max_hold_bars", None)
        if max_hold_bars is None:
            return None
        try:
            max_hold = int(max_hold_bars)
        except (TypeError, ValueError):
            return None
        if max_hold < 0:
            return None
        df = self._market_frame(
            frame,
            signal_time=getattr(signal, "ts", None),
            start_idx=start_idx,
            closed_only=closed_only,
            after_signal_time=after_signal_time,
        )
        if df.empty:
            return None

        entry_idx = 0 if start_idx is None else int(start_idx)
        if entry_idx < 0 or entry_idx >= len(df):
            return None
        entry_row = df.iloc[entry_idx]
        levels = self.levels_from_signal(signal, entry_price=entry_price if entry_price is not None else float(entry_row["open"]))
        if levels is None:
            return None

        if not include_entry_bar and max_hold <= 0:
            return None
        hold_span = max_hold if include_entry_bar else max_hold - 1
        end_idx = min(entry_idx + max(0, hold_span), len(df) - 1)
        result = self._scan_window(
            signal=signal,
            df=df,
            entry_idx=entry_idx,
            end_idx=end_idx,
            levels=levels,
            include_trend_reverse=include_trend_reverse,
        )
        if result.exit_reason == "max_hold" and require_complete_timeout and end_idx < entry_idx + max(0, hold_span):
            return None
        return result

    def simulate_levels(
        self,
        *,
        side: str,
        levels: SignalOutcomeLevels,
        frame: pd.DataFrame,
        start_idx: int,
        max_hold_bars: int,
        closed_only: bool = True,
        include_trend_reverse: bool = False,
    ) -> SignalOutcomeResult | None:
        df = self._market_frame(
            frame,
            signal_time=None,
            start_idx=start_idx,
            closed_only=closed_only,
            after_signal_time=False,
        )
        if df.empty or start_idx < 0 or start_idx >= len(df) or max_hold_bars < 0:
            return None
        signal = type("OutcomeSignal", (), {"side": side})()
        end_idx = min(start_idx + max_hold_bars, len(df) - 1)
        return self._scan_window(
            signal=signal,
            df=df,
            entry_idx=start_idx,
            end_idx=end_idx,
            levels=levels,
            include_trend_reverse=include_trend_reverse,
        )

    def _scan_window(
        self,
        *,
        signal: Any,
        df: pd.DataFrame,
        entry_idx: int,
        end_idx: int,
        levels: SignalOutcomeLevels,
        include_trend_reverse: bool,
    ) -> SignalOutcomeResult:
        side = str(getattr(signal, "side"))
        exit_idx = end_idx
        exit_price = float(df.iloc[end_idx]["close"])
        exit_reason: ExitReason = "max_hold"
        outcome: LabelOutcome = "TIMEOUT"

        for idx in range(entry_idx, end_idx + 1):
            row = df.iloc[idx]
            high = float(row["high"])
            low = float(row["low"])
            open_price = float(row["open"])
            if side == "long":
                if low <= levels.stop_loss:
                    exit_idx = idx
                    exit_price = min(float(levels.stop_loss), open_price)
                    exit_reason = "stop_loss"
                    outcome = "SL"
                    break
                if high >= levels.take_profit:
                    exit_idx = idx
                    exit_price = float(levels.take_profit)
                    exit_reason = "take_profit"
                    outcome = "TP"
                    break
                if include_trend_reverse and self._bias_at(row) == "short" and idx + 1 < len(df):
                    exit_idx = idx + 1
                    exit_price = float(df.iloc[exit_idx]["open"])
                    exit_reason = "trend_reverse"
                    outcome = "TIMEOUT"
                    break
            else:
                if high >= levels.stop_loss:
                    exit_idx = idx
                    exit_price = max(float(levels.stop_loss), open_price)
                    exit_reason = "stop_loss"
                    outcome = "SL"
                    break
                if low <= levels.take_profit:
                    exit_idx = idx
                    exit_price = float(levels.take_profit)
                    exit_reason = "take_profit"
                    outcome = "TP"
                    break
                if include_trend_reverse and self._bias_at(row) == "long" and idx + 1 < len(df):
                    exit_idx = idx + 1
                    exit_price = float(df.iloc[exit_idx]["open"])
                    exit_reason = "trend_reverse"
                    outcome = "TIMEOUT"
                    break

        observed = df.iloc[entry_idx : exit_idx + 1]
        if side == "long":
            mfe = float((observed["high"].max() - levels.entry_price) / levels.stop_dist)
            mae = float((observed["low"].min() - levels.entry_price) / levels.stop_dist)
        else:
            mfe = float((levels.entry_price - observed["low"].min()) / levels.stop_dist)
            mae = float((levels.entry_price - observed["high"].max()) / levels.stop_dist)
        return SignalOutcomeResult(
            outcome=outcome,
            exit_reason=exit_reason,
            entry_idx=int(entry_idx),
            exit_idx=int(exit_idx),
            entry_time=pd.Timestamp(df.iloc[entry_idx]["ts"]),
            exit_time=pd.Timestamp(df.iloc[exit_idx]["ts"]),
            entry_price=float(levels.entry_price),
            exit_price=float(exit_price),
            stop_loss=float(levels.stop_loss),
            take_profit=float(levels.take_profit),
            stop_dist=float(levels.stop_dist),
            mae=mae,
            mfe=mfe,
            holding_bars=int(exit_idx - entry_idx + 1),
        )

    @staticmethod
    def _market_frame(
        frame: pd.DataFrame,
        *,
        signal_time: Any,
        start_idx: int | None,
        closed_only: bool,
        after_signal_time: bool,
    ) -> pd.DataFrame:
        required = {"ts", "open", "high", "low", "close"}
        if frame.empty or not required.issubset(frame.columns):
            return pd.DataFrame()
        df = frame.copy()
        if closed_only and "is_closed" in df.columns:
            df = df[df["is_closed"].map(_is_closed_value)]
        if df.empty:
            return pd.DataFrame()
        df["ts"] = pd.to_datetime(df["ts"], utc=True, errors="coerce")
        for column in ["open", "high", "low", "close"]:
            df[column] = pd.to_numeric(df[column], errors="coerce")
        df = df.dropna(subset=["ts", "open", "high", "low", "close"]).sort_values("ts").reset_index(drop=True)
        if start_idx is None and after_signal_time:
            if signal_time is None:
                return pd.DataFrame()
            start = _utc_timestamp(signal_time)
            df = df[df["ts"] > start].reset_index(drop=True)
        return df

    @staticmethod
    def _bias_at(row: pd.Series) -> str:
        return str(row.get("trend_bias", row.get("bias_4h", "")))

    @staticmethod
    def closed_bars(frame: pd.DataFrame) -> pd.DataFrame:
        return SignalOutcomeSimulator._market_frame(
            frame,
            signal_time=None,
            start_idx=None,
            closed_only=True,
            after_signal_time=False,
        )


def _utc_timestamp(value: Any) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        return ts.tz_localize("UTC")
    return ts.tz_convert("UTC")


def _is_closed_value(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip().lower() not in {"", "0", "false", "no"}
    return bool(value)


__all__ = [
    "ExitReason",
    "LabelOutcome",
    "SignalOutcomeLevels",
    "SignalOutcomeResult",
    "SignalOutcomeSimulator",
]
