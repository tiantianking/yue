from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import pandas as pd

Side = Literal["long", "short", "flat"]

# 成交量过滤阈值（vol_ratio < 0.5 时不开仓）
VOL_RATIO_MIN = 0.5

# ATR 过滤阈值（过低不开仓）
ATR_PCT_MIN = 0.001

# 趋势强度评分阈值（EMA间距百分比）
TREND_STRENGTH_MIN = 0.005  # EMA间距至少0.5%才认为是强趋势

# 动量确认阈值
MOMENTUM_CONFIRM_BARS = 3  # 需要连续N根K线朝同一方向才确认动量


@dataclass(frozen=True)
class StrategyParams:
    fast_ema: int = 20
    slow_ema: int = 60
    breakout_window: int = 40
    atr_stop_mult: float = 2.0
    take_profit_mult: float = 2.0
    max_hold_bars: int = 48
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

    @property
    def accepted(self) -> bool:
        return self.side in {"long", "short"} and self.reject_reason is None


def _calculate_trend_strength(row: pd.Series) -> float:
    """
    计算趋势强度：EMA快速线与慢速线的间距百分比

    返回：
    - 正值：多头趋势强度
    - 负值：空头趋势强度
    - 接近0：趋势弱
    """
    ema_fast = row.get("ema_fast")
    ema_slow = row.get("ema_slow")
    close = row.get("close")

    if pd.isna(ema_fast) or pd.isna(ema_slow) or pd.isna(close) or close == 0:
        return 0.0

    # EMA间距百分比（相对于价格）
    spread_pct = (ema_fast - ema_slow) / close
    return float(spread_pct)


def _calculate_momentum(row: pd.Series, prev_rows: pd.DataFrame, side: str) -> int:
    """
    计算动量确认：连续朝同一方向的K线数量

    参数：
    - row: 当前K线
    - prev_rows: 前N根K线
    - side: 'long' 或 'short'

    返回：连续同向K线数量
    """
    if prev_rows.empty or len(prev_rows) < MOMENTUM_CONFIRM_BARS:
        return 0

    momentum = 0
    for _, prev_row in prev_rows.tail(MOMENTUM_CONFIRM_BARS).iterrows():
        if side == "long":
            if float(prev_row["close"]) > float(prev_row["open"]):
                momentum += 1
            else:
                break
        else:  # short
            if float(prev_row["close"]) < float(prev_row["open"]):
                momentum += 1
            else:
                break
    return momentum


def _identify_market_regime(frame: pd.DataFrame, idx: int, lookback: int = 20) -> str:
    """
    识别市场环境：高波动/低波动/趋势/震荡

    返回：
    - 'high_vol_trend': 高波动趋势市场（最有利）
    - 'low_vol_trend': 低波动趋势市场
    - 'high_vol_range': 高波动震荡市场（需谨慎）
    - 'low_vol_range': 低波动震荡市场
    """
    if idx < lookback:
        return "unknown"

    recent = frame.iloc[max(0, idx - lookback):idx + 1]
    close = recent["close"]
    high = recent["high"]
    low = recent["low"]

    # 计算波动率（ATR%）
    atr_val = atr(recent, 14).iloc[-1] if "atr" in recent.columns else 0
    atr_pct = atr_val / float(close.iloc[-1]) if close.iloc[-1] > 0 else 0

    # 计算趋势强度
    ema_fast = recent["ema_fast"].iloc[-1] if "ema_fast" in recent.columns else 0
    ema_slow = recent["ema_slow"].iloc[-1] if "ema_slow" in recent.columns else 0
    trend_strength = abs(ema_fast - ema_slow) / float(close.iloc[-1]) if close.iloc[-1] > 0 else 0

    # 识别市场环境
    avg_atr_pct = atr(frame.iloc[max(0, idx - 100):idx + 1], 14) / frame.iloc[max(0, idx - 100):idx + 1]["close"]
    current_vs_avg = atr_pct / avg_atr_pct.mean() if avg_atr_pct.mean() > 0 else 1

    is_high_vol = current_vs_avg > 1.5
    is_strong_trend = trend_strength > TREND_STRENGTH_MIN

    if is_high_vol and is_strong_trend:
        return "high_vol_trend"
    elif not is_high_vol and is_strong_trend:
        return "low_vol_trend"
    elif is_high_vol and not is_strong_trend:
        return "high_vol_range"
    else:
        return "low_vol_range"


def atr(frame: pd.DataFrame, window: int = 14) -> pd.Series:
    """计算ATR（简化版）"""
    prev_close = frame["close"].shift(1)
    tr = pd.concat([
        frame["high"] - frame["low"],
        (frame["high"] - prev_close).abs(),
        (frame["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(window=window, min_periods=window).mean()


def build_signal(row: pd.Series, *, inst_id: str, params: StrategyParams = StrategyParams(), frame: pd.DataFrame | None = None, idx: int = 0) -> TradeSignal:
    ts = pd.Timestamp(row["ts"])
    close = float(row["close"])
    atr = row.get("atr")
    bias = row.get("bias_4h", "flat")
    high_level = row.get("breakout_high")
    low_level = row.get("breakout_low")
    atr_pct = row.get("atr_pct")
    vol_ratio = row.get("vol_ratio")

    # 1. ATR 检查
    if pd.isna(atr) or atr <= 0:
        return TradeSignal(ts, inst_id, "flat", None, None, None, None, ("ATR_MISSING",), "atr_missing")

    # 2. ATR% 过低检查
    if not pd.isna(atr_pct) and atr_pct < ATR_PCT_MIN:
        return TradeSignal(ts, inst_id, "flat", None, None, None, None, ("ATR_PCT_LOW",), "atr_pct_too_low")

    # 3. 4h 方向检查
    if bias not in {"long", "short"}:
        return TradeSignal(ts, inst_id, "flat", None, None, None, None, ("4H_FLAT",), "flat_4h_bias")

    # 4. 突破位检查
    if pd.isna(high_level) or pd.isna(low_level):
        return TradeSignal(ts, inst_id, "flat", None, None, None, None, ("BREAKOUT_MISSING",), "breakout_missing")

    # 5. 成交量过滤
    if not pd.isna(vol_ratio) and vol_ratio < VOL_RATIO_MIN:
        return TradeSignal(ts, inst_id, "flat", None, None, None, None, ("VOL_LOW",), "volume_too_low")

    # 6. 趋势强度检查（新增）
    trend_strength = _calculate_trend_strength(row)
    if abs(trend_strength) < TREND_STRENGTH_MIN:
        return TradeSignal(ts, inst_id, "flat", None, None, None, None, ("TREND_WEAK",), "trend_strength_too_weak")

    # 7. 市场环境识别（新增）- 高波动趋势市场最有利
    if frame is not None:
        market_regime = _identify_market_regime(frame, idx)
        # 在高波动震荡市场降低信号权重，但不拒绝
        if market_regime == "high_vol_range":
            # 可以在这里添加日志或调整参数
            pass

    # 8. 多头突破信号
    if bias == "long" and close > float(high_level):
        stop_dist = float(atr) * params.atr_stop_mult
        return TradeSignal(
            ts=ts,
            inst_id=inst_id,
            side="long",
            entry_ref=close,
            stop_loss=close - stop_dist,
            take_profit=close + stop_dist * params.take_profit_mult,
            max_hold_bars=params.max_hold_bars,
            reason_codes=("4H_TREND_LONG", "1H_BREAKOUT_UP", "ATR_OK", "VOL_OK", "TREND_STRONG"),
        )

    # 9. 空头突破信号
    if bias == "short" and close < float(low_level):
        stop_dist = float(atr) * params.atr_stop_mult
        return TradeSignal(
            ts=ts,
            inst_id=inst_id,
            side="short",
            entry_ref=close,
            stop_loss=close + stop_dist,
            take_profit=close - stop_dist * params.take_profit_mult,
            max_hold_bars=params.max_hold_bars,
            reason_codes=("4H_TREND_SHORT", "1H_BREAKOUT_DOWN", "ATR_OK", "VOL_OK", "TREND_STRONG"),
        )

    return TradeSignal(ts, inst_id, "flat", None, None, None, None, ("NO_BREAKOUT",), "no_breakout")


def generate_signals(features: pd.DataFrame, *, inst_id: str, params: StrategyParams = StrategyParams()) -> list[TradeSignal]:
    """
    生成交易信号列表

    改进：增加市场环境识别和趋势强度过滤
    """
    signals = []
    for idx, (_, row) in enumerate(features.iterrows()):
        signal = build_signal(row, inst_id=inst_id, params=params, frame=features, idx=idx)
        signals.append(signal)
    return signals
