from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
import pandas as pd

Side = Literal["long", "short", "flat"]

VOL_RATIO_MIN = 0.5
ATR_PCT_MIN = 0.001
TREND_STRENGTH_MIN = 0.005
MOMENTUM_CONFIRM_BARS = 3
PULLBACK_LOOKBACK_BARS = 8
PULLBACK_ATR_BAND = 0.5
PULLBACK_DEEP_ATR_LIMIT = 1.5
PULLBACK_RECLAIM_MIN_ATR = 0.15
MAX_EXTENSION_ATR = 1.35
CONTINUATION_TREND_STRENGTH_MIN = 0.01
CONTINUATION_VOL_RATIO_MIN = 0.9
MIN_CONTINUATION_SCORE = 9.6

# Conservative live-signal protection floors. They include room for OKX fees,
# slippage, and normal intraday noise so alerts do not suggest tiny TP/SL bands.
COST_BUFFER_RATE = 0.002
MIN_STOP_DISTANCE_PCT = 0.004
MIN_TAKE_PROFIT_DISTANCE_PCT = 0.008
MIN_REWARD_TO_RISK = 3.5


@dataclass(frozen=True)
class StrategyParams:
    fast_ema: int = 120
    slow_ema: int = 720
    breakout_window: int = 384
    atr_stop_mult: float = 4.0
    take_profit_mult: float = 6.0
    max_hold_bars: int = 768
    atr_window: int = 14


@dataclass(frozen=True)
class TradeSignal:
    ts: pd.Timestamp
    inst_id: str
    side: Side
    entry_ref: float | None
    stop_loss: float | None
    take_profit: float | None
    max_hold_bars: int | None
    reason_codes: tuple[str, ...]
    reject_reason: str | None = None
    signal_score: float | None = None
    risk_reward_ratio: float | None = None
    stop_reason: str | None = None
    tp_reason: str | None = None

    @property
    def accepted(self) -> bool:
        return self.side in {"long", "short"} and self.reject_reason is None


def _reject(
    ts: pd.Timestamp,
    inst_id: str,
    code: str,
    reason: str,
    *,
    score: float | None = None,
) -> TradeSignal:
    return TradeSignal(
        ts=ts,
        inst_id=inst_id,
        side="flat",
        entry_ref=None,
        stop_loss=None,
        take_profit=None,
        max_hold_bars=None,
        reason_codes=(code,),
        reject_reason=reason,
        signal_score=score,
    )


def _bounded_score(value: float) -> float:
    if not np.isfinite(value):
        return 1.0
    return float(max(1.0, min(10.0, value)))


def _num(row: pd.Series, name: str, default: float = np.nan) -> float:
    value = row.get(name, default)
    if pd.isna(value):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _calculate_trend_strength(row: pd.Series) -> float:
    ema_fast = _num(row, "ema_fast")
    ema_slow = _num(row, "ema_slow")
    close = _num(row, "close")
    if not np.isfinite(ema_fast) or not np.isfinite(ema_slow) or close <= 0:
        return 0.0
    return float((ema_fast - ema_slow) / close)


def _calculate_momentum(row: pd.Series, prev_rows: pd.DataFrame, side: str) -> int:
    if prev_rows.empty or len(prev_rows) < MOMENTUM_CONFIRM_BARS:
        return 0

    momentum = 0
    for _, prev_row in prev_rows.tail(MOMENTUM_CONFIRM_BARS).iterrows():
        if side == "long" and float(prev_row["close"]) > float(prev_row["open"]):
            momentum += 1
        elif side == "short" and float(prev_row["close"]) < float(prev_row["open"]):
            momentum += 1
        else:
            break
    return momentum


def atr(frame: pd.DataFrame, window: int = 14) -> pd.Series:
    prev_close = frame["close"].shift(1)
    tr = pd.concat(
        [
            frame["high"] - frame["low"],
            (frame["high"] - prev_close).abs(),
            (frame["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.rolling(window=window, min_periods=window).mean()


def _identify_market_regime(frame: pd.DataFrame, idx: int, lookback: int = 20) -> str:
    if frame is not None and 0 <= idx < len(frame) and "market_regime" in frame.columns:
        regime = str(frame.iloc[idx].get("market_regime", "unknown"))
        if regime in {"high_vol_trend", "low_vol_trend", "high_vol_range", "low_vol_range", "unknown"}:
            return regime

    if idx < lookback:
        return "unknown"

    recent = frame.iloc[max(0, idx - lookback) : idx + 1]
    close = float(recent["close"].iloc[-1])
    if close <= 0:
        return "unknown"

    atr_value = _num(recent.iloc[-1], "atr", 0.0)
    if atr_value <= 0:
        atr_value = float(atr(recent, 14).iloc[-1]) if len(recent) >= 14 else 0.0
    atr_pct = atr_value / close if close > 0 else 0.0

    ema_fast = _num(recent.iloc[-1], "ema_fast", 0.0)
    ema_slow = _num(recent.iloc[-1], "ema_slow", 0.0)
    trend_strength = abs(ema_fast - ema_slow) / close if close > 0 else 0.0

    lookback_frame = frame.iloc[max(0, idx - 100) : idx + 1]
    if "atr" in lookback_frame.columns:
        avg_atr_pct = pd.to_numeric(lookback_frame["atr"], errors="coerce") / lookback_frame["close"].astype(float)
    else:
        avg_atr_pct = atr(lookback_frame, 14) / lookback_frame["close"].astype(float)
    avg = float(avg_atr_pct.replace([np.inf, -np.inf], np.nan).dropna().mean() or 0.0)
    current_vs_avg = atr_pct / avg if avg > 0 else 1.0

    is_high_vol = current_vs_avg > 1.5
    is_strong_trend = trend_strength > TREND_STRENGTH_MIN
    if is_high_vol and is_strong_trend:
        return "high_vol_trend"
    if not is_high_vol and is_strong_trend:
        return "low_vol_trend"
    if is_high_vol and not is_strong_trend:
        return "high_vol_range"
    return "low_vol_range"


def _score_breakout(
    *,
    side: str,
    close: float,
    breakout_level: float,
    trend_strength: float,
    atr_pct: float,
    vol_ratio: float,
    reward_to_risk: float,
    market_regime: str,
    frame: pd.DataFrame | None,
    idx: int,
) -> float:
    signed_strength = trend_strength if side == "long" else -trend_strength
    trend_bonus = min(2.0, max(0.0, signed_strength / 0.025 * 2.0))
    breakout_pct = abs(close - breakout_level) / close if close > 0 else 0.0
    breakout_bonus = min(1.2, breakout_pct / max(atr_pct, 0.0001) * 0.6)
    volume_bonus = min(1.0, max(0.0, (vol_ratio - 0.8) / 1.2))
    rr_bonus = min(1.0, max(0.0, (reward_to_risk - MIN_REWARD_TO_RISK) / 1.5))

    regime_bonus = {
        "high_vol_trend": 0.6,
        "low_vol_trend": 0.2,
        "high_vol_range": -0.8,
        "low_vol_range": -1.2,
        "unknown": -0.2,
    }.get(market_regime, 0.0)

    momentum_bonus = 0.0
    if frame is not None and idx > 0:
        momentum = _calculate_momentum(pd.Series(dtype=float), frame.iloc[max(0, idx - 5) : idx], side)
        momentum_bonus = min(0.6, momentum * 0.2)

    return _bounded_score(4.8 + trend_bonus + breakout_bonus + volume_bonus + rr_bonus + regime_bonus + momentum_bonus)


def _score_continuation(
    *,
    side: str,
    close: float,
    ema_fast: float,
    trend_strength: float,
    atr_pct: float,
    vol_ratio: float,
    reward_to_risk: float,
    market_regime: str,
    frame: pd.DataFrame | None,
    idx: int,
) -> float:
    signed_strength = trend_strength if side == "long" else -trend_strength
    trend_bonus = min(1.8, max(0.0, signed_strength / 0.025 * 1.8))
    reclaim_pct = abs(close - ema_fast) / close if close > 0 else 0.0
    reclaim_bonus = min(0.9, max(0.0, reclaim_pct / max(atr_pct, 0.0001) * 0.45))
    volume_bonus = min(0.8, max(0.0, (vol_ratio - 0.8) / 1.5))
    rr_bonus = min(1.0, max(0.0, (reward_to_risk - MIN_REWARD_TO_RISK) / 1.5))
    regime_bonus = {
        "high_vol_trend": 0.7,
        "low_vol_trend": 0.4,
        "high_vol_range": -0.7,
        "low_vol_range": -1.4,
        "unknown": -0.2,
    }.get(market_regime, 0.0)
    momentum_bonus = 0.0
    if frame is not None and idx > 0:
        momentum = _calculate_momentum(pd.Series(dtype=float), frame.iloc[max(0, idx - 5) : idx], side)
        momentum_bonus = min(0.6, momentum * 0.2)
    return _bounded_score(5.4 + trend_bonus + reclaim_bonus + volume_bonus + rr_bonus + regime_bonus + momentum_bonus)


def _continuation_side(row: pd.Series, frame: pd.DataFrame | None, idx: int, *, bias: str, trend_strength: float) -> Side:
    if frame is None or idx < 2 or bias not in {"long", "short"}:
        return "flat"

    close = _num(row, "close")
    open_ = _num(row, "open")
    ema_fast = _num(row, "ema_fast")
    ema_slow = _num(row, "ema_slow")
    atr_value = _num(row, "atr")
    vol_ratio = _num(row, "vol_ratio", 1.0)
    if not all(np.isfinite(value) for value in [close, open_, ema_fast, ema_slow, atr_value]) or atr_value <= 0:
        return "flat"
    if np.isfinite(vol_ratio) and vol_ratio < CONTINUATION_VOL_RATIO_MIN:
        return "flat"

    recent = frame.iloc[max(0, idx - PULLBACK_LOOKBACK_BARS) : idx + 1]
    if len(recent) < 3:
        return "flat"
    prev_close = _num(frame.iloc[idx - 1], "close")

    if bias == "long":
        recent_low = float(recent["low"].astype(float).min())
        extension = close - ema_fast
        touched_fast = recent_low <= ema_fast + atr_value * PULLBACK_ATR_BAND
        not_too_deep = recent_low >= ema_fast - atr_value * PULLBACK_DEEP_ATR_LIMIT
        reclaimed = close > ema_fast and close > open_ and close > prev_close
        not_extended = atr_value * PULLBACK_RECLAIM_MIN_ATR <= extension <= atr_value * MAX_EXTENSION_ATR
        if trend_strength > CONTINUATION_TREND_STRENGTH_MIN and ema_fast > ema_slow and touched_fast and not_too_deep and reclaimed and not_extended:
            return "long"
    else:
        recent_high = float(recent["high"].astype(float).max())
        extension = ema_fast - close
        touched_fast = recent_high >= ema_fast - atr_value * PULLBACK_ATR_BAND
        not_too_deep = recent_high <= ema_fast + atr_value * PULLBACK_DEEP_ATR_LIMIT
        reclaimed = close < ema_fast and close < open_ and close < prev_close
        not_extended = atr_value * PULLBACK_RECLAIM_MIN_ATR <= extension <= atr_value * MAX_EXTENSION_ATR
        if trend_strength < -CONTINUATION_TREND_STRENGTH_MIN and ema_fast < ema_slow and touched_fast and not_too_deep and reclaimed and not_extended:
            return "short"
    return "flat"


def _protection_reject_reason(*, close: float, stop_dist: float, tp_dist: float) -> str | None:
    if close <= 0 or stop_dist <= 0 or tp_dist <= 0:
        return "invalid_trade_protection"

    stop_pct = stop_dist / close
    tp_pct = tp_dist / close
    min_stop = max(MIN_STOP_DISTANCE_PCT, COST_BUFFER_RATE * 2.0)
    min_tp = max(MIN_TAKE_PROFIT_DISTANCE_PCT, min_stop * MIN_REWARD_TO_RISK)
    rr = tp_dist / stop_dist

    if stop_pct < min_stop:
        return "stop_distance_too_close"
    if tp_pct < min_tp:
        return "take_profit_too_close"
    if rr + 1e-9 < MIN_REWARD_TO_RISK:
        return "risk_reward_too_low"
    return None


def build_signal(
    row: pd.Series,
    *,
    inst_id: str,
    params: StrategyParams = StrategyParams(),
    frame: pd.DataFrame | None = None,
    idx: int = 0,
) -> TradeSignal:
    ts = pd.Timestamp(row["ts"])
    close = _num(row, "close")
    atr_value = _num(row, "atr")
    bias = row.get("trend_bias", row.get("bias_4h", "flat"))
    signal_tf = str(row.get("signal_timeframe", "1h")).upper()
    trend_tf = str(row.get("trend_timeframe", "4h")).upper()
    high_level = _num(row, "breakout_high")
    low_level = _num(row, "breakout_low")
    atr_pct = _num(row, "atr_pct", atr_value / close if close > 0 else np.nan)
    vol_ratio = _num(row, "vol_ratio", 1.0)

    if not np.isfinite(close) or close <= 0:
        return _reject(ts, inst_id, "PRICE_MISSING", "price_missing")
    if not np.isfinite(atr_value) or atr_value <= 0:
        return _reject(ts, inst_id, "ATR_MISSING", "atr_missing")
    if np.isfinite(atr_pct) and atr_pct < ATR_PCT_MIN:
        return _reject(ts, inst_id, "ATR_PCT_LOW", "atr_pct_too_low")
    if bias not in {"long", "short"}:
        return _reject(ts, inst_id, "TREND_FLAT", "flat_trend_bias")
    if not np.isfinite(high_level) or not np.isfinite(low_level):
        return _reject(ts, inst_id, "BREAKOUT_MISSING", "breakout_missing")
    if np.isfinite(vol_ratio) and vol_ratio < VOL_RATIO_MIN:
        return _reject(ts, inst_id, "VOL_LOW", "volume_too_low")

    trend_strength = _calculate_trend_strength(row)
    if abs(trend_strength) < TREND_STRENGTH_MIN:
        return _reject(ts, inst_id, "TREND_WEAK", "trend_strength_too_weak")

    market_regime = _identify_market_regime(frame, idx) if frame is not None else "unknown"
    stop_dist = float(atr_value) * params.atr_stop_mult
    tp_dist = stop_dist * params.take_profit_mult
    protection_reason = _protection_reject_reason(close=close, stop_dist=stop_dist, tp_dist=tp_dist)

    if bias == "long" and close > high_level:
        rr = tp_dist / stop_dist if stop_dist > 0 else 0.0
        score = _score_breakout(
            side="long",
            close=close,
            breakout_level=high_level,
            trend_strength=trend_strength,
            atr_pct=float(atr_pct) if np.isfinite(atr_pct) else stop_dist / close,
            vol_ratio=float(vol_ratio) if np.isfinite(vol_ratio) else 1.0,
            reward_to_risk=rr,
            market_regime=market_regime,
            frame=frame,
            idx=idx,
        )
        if protection_reason:
            return _reject(ts, inst_id, "PROTECTION_TOO_CLOSE", protection_reason, score=score)
        return TradeSignal(
            ts=ts,
            inst_id=inst_id,
            side="long",
            entry_ref=close,
            stop_loss=close - stop_dist,
            take_profit=close + tp_dist,
            max_hold_bars=params.max_hold_bars,
            reason_codes=(f"{trend_tf}_TREND_LONG", f"{signal_tf}_BREAKOUT_UP", "ATR_OK", "VOL_OK", "TREND_STRONG"),
            signal_score=score,
            risk_reward_ratio=rr,
            stop_reason=f"ATR {params.atr_stop_mult:g}x with fee/slippage floor",
            tp_reason=f"RR {rr:.2f}:1 after protection floor",
        )

    if bias == "short" and close < low_level:
        rr = tp_dist / stop_dist if stop_dist > 0 else 0.0
        score = _score_breakout(
            side="short",
            close=close,
            breakout_level=low_level,
            trend_strength=trend_strength,
            atr_pct=float(atr_pct) if np.isfinite(atr_pct) else stop_dist / close,
            vol_ratio=float(vol_ratio) if np.isfinite(vol_ratio) else 1.0,
            reward_to_risk=rr,
            market_regime=market_regime,
            frame=frame,
            idx=idx,
        )
        if protection_reason:
            return _reject(ts, inst_id, "PROTECTION_TOO_CLOSE", protection_reason, score=score)
        return TradeSignal(
            ts=ts,
            inst_id=inst_id,
            side="short",
            entry_ref=close,
            stop_loss=close + stop_dist,
            take_profit=close - tp_dist,
            max_hold_bars=params.max_hold_bars,
            reason_codes=(f"{trend_tf}_TREND_SHORT", f"{signal_tf}_BREAKOUT_DOWN", "ATR_OK", "VOL_OK", "TREND_STRONG"),
            signal_score=score,
            risk_reward_ratio=rr,
            stop_reason=f"ATR {params.atr_stop_mult:g}x with fee/slippage floor",
            tp_reason=f"RR {rr:.2f}:1 after protection floor",
        )

    continuation = _continuation_side(row, frame, idx, bias=bias, trend_strength=trend_strength)
    if continuation in {"long", "short"}:
        rr = tp_dist / stop_dist if stop_dist > 0 else 0.0
        ema_fast = _num(row, "ema_fast")
        score = _score_continuation(
            side=continuation,
            close=close,
            ema_fast=ema_fast,
            trend_strength=trend_strength,
            atr_pct=float(atr_pct) if np.isfinite(atr_pct) else stop_dist / close,
            vol_ratio=float(vol_ratio) if np.isfinite(vol_ratio) else 1.0,
            reward_to_risk=rr,
            market_regime=market_regime,
            frame=frame,
            idx=idx,
        )
        if score < MIN_CONTINUATION_SCORE:
            return _reject(ts, inst_id, "CONTINUATION_SCORE_LOW", "continuation_score_too_low", score=score)
        if protection_reason:
            return _reject(ts, inst_id, "PROTECTION_TOO_CLOSE", protection_reason, score=score)
        if continuation == "long":
            stop_loss = close - stop_dist
            take_profit = close + tp_dist
            reason_codes = (f"{trend_tf}_TREND_LONG", f"{signal_tf}_PULLBACK_RECLAIM_UP", "ATR_OK", "VOL_OK", "TREND_STRONG")
        else:
            stop_loss = close + stop_dist
            take_profit = close - tp_dist
            reason_codes = (f"{trend_tf}_TREND_SHORT", f"{signal_tf}_PULLBACK_RECLAIM_DOWN", "ATR_OK", "VOL_OK", "TREND_STRONG")
        return TradeSignal(
            ts=ts,
            inst_id=inst_id,
            side=continuation,
            entry_ref=close,
            stop_loss=stop_loss,
            take_profit=take_profit,
            max_hold_bars=params.max_hold_bars,
            reason_codes=reason_codes,
            signal_score=score,
            risk_reward_ratio=rr,
            stop_reason=f"ATR {params.atr_stop_mult:g}x pullback continuation stop",
            tp_reason=f"RR {rr:.2f}:1 continuation target",
        )

    return _reject(ts, inst_id, "NO_BREAKOUT", "no_breakout")


def generate_signals(features: pd.DataFrame, *, inst_id: str, params: StrategyParams = StrategyParams()) -> list[TradeSignal]:
    return [
        build_signal(row, inst_id=inst_id, params=params, frame=features, idx=idx)
        for idx, (_, row) in enumerate(features.iterrows())
    ]
