from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from okx_signal_system.features.indicators import build_feature_frame, atr
from okx_signal_system.risk.costs import estimate_costs, participation_rate, slippage_bps_for_participation
from okx_signal_system.risk.model import (
    COOL_OFF_BARS,
    EXTREME_VOLATILITY_THRESHOLD,
    Ledger,
    RiskConfig,
    validate_signal,
)
from okx_signal_system.strategy.trend_breakout import StrategyParams, build_signal


@dataclass(frozen=True)
class TradeRecord:
    inst_id: str
    entry_time: str
    exit_time: str
    side: str
    entry_price: float
    exit_price: float
    stop_loss: float
    take_profit: float
    max_hold_bars: int
    qty: float
    risk_amount: float
    notional: float
    gross_pnl: float
    costs: float
    net_pnl: float
    exit_reason: str
    leverage_cap: float
    leverage_used: float
    stop_distance_pct: float
    est_liq_buffer_pct: float
    near_liq_flag: bool


def split_train_valid(frame: pd.DataFrame, *, valid_fraction: float = 0.25) -> tuple[pd.DataFrame, pd.DataFrame]:
    if not 0 < valid_fraction < 1:
        raise ValueError("valid_fraction must be between 0 and 1")
    split_at = int(len(frame) * (1 - valid_fraction))
    return frame.iloc[:split_at].reset_index(drop=True), frame.iloc[split_at:].reset_index(drop=True)


def detect_cool_off_condition(features: pd.DataFrame, idx: int, atr_window: int = 14) -> bool:
    """检测当前bar是否处于极端波动状态（ATR%远超历史均值），触发冷静期"""
    if idx < atr_window:
        return False
    recent_frame = features.iloc[idx : idx + 1][["high", "low", "close"]].copy()
    if len(recent_frame) == 0:
        return False
    # 计算最近 atr_window 根 bar 的平均 ATR%
    window_frame = features.iloc[max(0, idx - atr_window) : idx][["high", "low", "close"]].copy()
    if len(window_frame) < atr_window:
        return False
    window_atr = atr(window_frame, atr_window)
    mean_close = window_frame["close"].mean()
    if mean_close <= 0:
        return False
    mean_atr_pct = window_atr.mean() / mean_close
    # 当前bar的ATR%
    cur_atr = atr(pd.concat([window_frame, recent_frame], axis=0), atr_window).iloc[-1]
    cur_atr_pct = float(cur_atr) / float(recent_frame["close"].iloc[0]) if recent_frame["close"].iloc[0] > 0 else 0
    # 如果当前bar的ATR%超过历史均值 * EXTREME_VOLATILITY_THRESHOLD，触发冷静期
    return cur_atr_pct > EXTREME_VOLATILITY_THRESHOLD * mean_atr_pct


def exit_trade(features: pd.DataFrame, entry_idx: int, signal, params: StrategyParams) -> tuple[int, float, str]:
    end_idx = min(entry_idx + params.max_hold_bars, len(features) - 1)
    for idx in range(entry_idx + 1, end_idx + 1):
        row = features.iloc[idx]
        if signal.side == "long":
            # 保守处理：同bar同时触及TP和SL时，默认先触发止损
            if row["low"] <= signal.stop_loss:
                return idx, float(signal.stop_loss), "stop_loss"
            if row["high"] >= signal.take_profit:
                return idx, float(signal.take_profit), "take_profit"
            if row.get("bias_4h") == "short" and idx + 1 < len(features):
                return idx + 1, float(features.iloc[idx + 1]["open"]), "trend_reverse"
        if signal.side == "short":
            if row["high"] >= signal.stop_loss:
                return idx, float(signal.stop_loss), "stop_loss"
            if row["low"] <= signal.take_profit:
                return idx, float(signal.take_profit), "take_profit"
            if row.get("bias_4h") == "long" and idx + 1 < len(features):
                return idx + 1, float(features.iloc[idx + 1]["open"]), "trend_reverse"
    return end_idx, float(features.iloc[end_idx]["open"]), "max_hold"


def exit_trade_from_arrays(
    *,
    high: np.ndarray,
    low: np.ndarray,
    open_: np.ndarray,
    bias: np.ndarray,
    entry_idx: int,
    side: str,
    stop_loss: float,
    take_profit: float,
    max_hold_bars: int,
) -> tuple[int, float, str]:
    end_idx = min(entry_idx + max_hold_bars, len(open_) - 1)
    for idx in range(entry_idx + 1, end_idx + 1):
        if side == "long":
            # 保守处理：同bar同时触及TP和SL时，默认先触发止损
            if low[idx] <= stop_loss:
                return idx, float(stop_loss), "stop_loss"
            if high[idx] >= take_profit:
                return idx, float(take_profit), "take_profit"
            if bias[idx] == "short" and idx + 1 < len(open_):
                return idx + 1, float(open_[idx + 1]), "trend_reverse"
        else:
            if high[idx] >= stop_loss:
                return idx, float(stop_loss), "stop_loss"
            if low[idx] <= take_profit:
                return idx, float(take_profit), "take_profit"
            if bias[idx] == "long" and idx + 1 < len(open_):
                return idx + 1, float(open_[idx + 1]), "trend_reverse"
    return end_idx, float(open_[end_idx]), "max_hold"


def run_backtest(
    frame_1h: pd.DataFrame,
    *,
    inst_id: str,
    params: StrategyParams = StrategyParams(),
    risk_config: RiskConfig = RiskConfig(),
) -> pd.DataFrame:
    features = build_feature_frame(
        frame_1h,
        fast_ema=params.fast_ema,
        slow_ema=params.slow_ema,
        breakout_window=params.breakout_window,
        atr_window=params.atr_window,
    ).reset_index(drop=True)
    return run_backtest_from_features(features, inst_id=inst_id, params=params, risk_config=risk_config)


def run_backtest_from_features(
    features: pd.DataFrame,
    *,
    inst_id: str,
    params: StrategyParams = StrategyParams(),
    risk_config: RiskConfig = RiskConfig(),
) -> pd.DataFrame:
    features = features.reset_index(drop=True)
    ledger = Ledger(
        inst_id=inst_id,
        init_capital=risk_config.initial_equity,
        equity=risk_config.initial_equity,
        cool_off_bars=0,  # 初始化冷静期计数器
    )
    trades: list[TradeRecord] = []
    if len(features) < 3:
        return pd.DataFrame()

    ts = features["ts"].to_numpy()
    open_ = features["open"].to_numpy(dtype=float)
    high = features["high"].to_numpy(dtype=float)
    low = features["low"].to_numpy(dtype=float)
    close = features["close"].to_numpy(dtype=float)
    volume = features["volume"].to_numpy(dtype=float)
    atr = features["atr"].to_numpy(dtype=float)
    breakout_high = features["breakout_high"].to_numpy(dtype=float)
    breakout_low = features["breakout_low"].to_numpy(dtype=float)
    bias = features["bias_4h"].astype(str).to_numpy()
    vol_ratio = features["vol_ratio"].to_numpy(dtype=float) if "vol_ratio" in features.columns else np.full(len(features), np.nan)

    # 生成候选信号mask（添加vol_ratio过滤，阈值0.5）
    vol_ok = np.isfinite(vol_ratio) & (vol_ratio >= 0.5)
    long_mask = (bias == "long") & np.isfinite(atr) & (atr > 0) & np.isfinite(breakout_high) & (close > breakout_high) & vol_ok
    short_mask = (bias == "short") & np.isfinite(atr) & (atr > 0) & np.isfinite(breakout_low) & (close < breakout_low) & vol_ok
    candidate_indices = np.flatnonzero((long_mask | short_mask) & (np.arange(len(features)) < len(features) - 2))

    cursor = 0
    for idx in candidate_indices:
        if idx < cursor:
            continue

        # 冷静期检查：如果当前bar处于冷静期，跳过
        if ledger.cool_off_bars > 0:
            # 冷静期递减
            ledger = Ledger(
                inst_id=inst_id,
                init_capital=ledger.init_capital,
                equity=ledger.equity,
                open_positions=ledger.open_positions,
                status=ledger.status,
                loss_streak=ledger.loss_streak,
                max_drawdown=ledger.max_drawdown,
                cool_off_bars=ledger.cool_off_bars - 1,
            )
            continue

        # 检测冷静期条件：最近3根bar有连续极端波动
        if detect_cool_off_condition(features, idx, atr_window=params.atr_window):
            # 进入冷静期，跳过这个信号
            ledger = Ledger(
                inst_id=inst_id,
                init_capital=ledger.init_capital,
                equity=ledger.equity,
                open_positions=ledger.open_positions,
                status=ledger.status,
                loss_streak=ledger.loss_streak,
                max_drawdown=ledger.max_drawdown,
                cool_off_bars=COOL_OFF_BARS,  # 设置冷静期
            )
            continue

        side = "long" if long_mask[idx] else "short"
        stop_dist = float(atr[idx]) * params.atr_stop_mult
        stop_loss = float(close[idx] - stop_dist if side == "long" else close[idx] + stop_dist)
        take_profit = float(close[idx] + stop_dist * params.take_profit_mult if side == "long" else close[idx] - stop_dist * params.take_profit_mult)
        signal = build_signal(features.iloc[idx], inst_id=inst_id, params=params)
        decision = validate_signal(signal, ledger, risk_config)
        if not decision.accepted or decision.qty is None:
            continue
        entry_idx = idx + 1
        entry_price = float(open_[entry_idx])
        notional = abs(entry_price * decision.qty)
        try:
            slip_bps = slippage_bps_for_participation(
                participation_rate(notional=notional, close=entry_price, volume=float(volume[entry_idx]))
            )
        except ValueError:
            continue
        exit_idx, exit_price, exit_reason = exit_trade_from_arrays(
            high=high,
            low=low,
            open_=open_,
            bias=bias,
            entry_idx=entry_idx,
            side=side,
            stop_loss=stop_loss,
            take_profit=take_profit,
            max_hold_bars=params.max_hold_bars,
        )

        side_mult = 1 if signal.side == "long" else -1
        gross_pnl = (exit_price - entry_price) * decision.qty * side_mult
        costs = estimate_costs(
            entry_price=entry_price,
            exit_price=exit_price,
            qty=decision.qty,
            entry_time=pd.Timestamp(ts[entry_idx]),
            exit_time=pd.Timestamp(ts[exit_idx]),
            slippage_bps=slip_bps,
        )
        net_pnl = gross_pnl - costs.total

        # 更新ledger状态
        new_equity = ledger.equity + net_pnl
        new_loss_streak = ledger.loss_streak + 1 if net_pnl < 0 else 0
        new_max_drawdown = max(
            ledger.max_drawdown,
            max(0.0, (ledger.init_capital - new_equity) / ledger.init_capital),
        )
        # 交易后冷静期归零（无论盈亏）
        ledger = Ledger(
            inst_id=inst_id,
            init_capital=ledger.init_capital,
            equity=new_equity,
            open_positions=ledger.open_positions,
            status=ledger.status,
            loss_streak=new_loss_streak,
            max_drawdown=new_max_drawdown,
            cool_off_bars=0,
        )
        trades.append(
            TradeRecord(
                inst_id=inst_id,
                entry_time=pd.Timestamp(ts[entry_idx]).isoformat(),
                exit_time=pd.Timestamp(ts[exit_idx]).isoformat(),
                side=signal.side,
                entry_price=entry_price,
                exit_price=exit_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                max_hold_bars=int(params.max_hold_bars),
                qty=float(decision.qty),
                risk_amount=float(decision.risk_amount or 0.0),
                notional=float(decision.notional or notional),
                gross_pnl=float(gross_pnl),
                costs=float(costs.total),
                net_pnl=float(net_pnl),
                exit_reason=exit_reason,
                leverage_cap=float(decision.leverage_cap),
                leverage_used=float(decision.leverage_used or 0.0),
                stop_distance_pct=float(decision.stop_distance_pct or 0.0),
                est_liq_buffer_pct=float(decision.est_liq_buffer_pct or 0.0),
                near_liq_flag=bool(decision.near_liq_flag),
            )
        )
        cursor = max(exit_idx + 1, idx + 1)
    return pd.DataFrame([asdict(trade) for trade in trades])


def summarize_trades(trades: pd.DataFrame, *, initial_equity: float = 10000.0) -> dict[str, float | int | str]:
    if trades.empty:
        return {
            "total_return": 0.0,
            "profit_factor": 0.0,
            "payoff_ratio": 0.0,
            "win_rate": 0.0,
            "total_trades": 0,
            "max_drawdown": 0.0,
            "avg_hold_hours": 0.0,
            "max_loss_streak": 0,
            "hit_27pct_stop": 0,
            "pnl_share_from_gt5x": 0.0,
            "near_liq_trades": 0,
            "gt5x_trade_pct": 0.0,
            "status": "failed_no_trades",
        }
    pnl = trades["net_pnl"]
    wins = pnl[pnl > 0].sum()
    losses = pnl[pnl < 0].sum()
    avg_win = pnl[pnl > 0].mean() if (pnl > 0).any() else 0.0
    avg_loss = pnl[pnl < 0].mean() if (pnl < 0).any() else 0.0
    total = trades["net_pnl"].sum()
    equity = initial_equity + pnl.cumsum()
    running_peak = equity.cummax()
    drawdown = (running_peak - equity) / running_peak
    loss_streak = 0
    max_loss_streak = 0
    for value in pnl:
        loss_streak = loss_streak + 1 if value < 0 else 0
        max_loss_streak = max(max_loss_streak, loss_streak)
    entry = pd.to_datetime(trades["entry_time"], utc=True)
    exit_ = pd.to_datetime(trades["exit_time"], utc=True)
    gt5_profit = trades[(trades["leverage_cap"] > 5) & (trades["net_pnl"] > 0)]["net_pnl"].sum()
    gt5_trades = (trades["leverage_cap"] > 5).mean()
    return {
        "total_return": float(total / initial_equity),
        "profit_factor": float(wins / abs(losses)) if losses < 0 else float("inf"),
        "payoff_ratio": float(avg_win / abs(avg_loss)) if avg_loss < 0 else float("inf"),
        "win_rate": float((trades["net_pnl"] > 0).mean()),
        "total_trades": int(len(trades)),
        "max_drawdown": float(drawdown.max()) if not drawdown.empty else 0.0,
        "avg_hold_hours": float(((exit_ - entry).dt.total_seconds() / 3600).mean()),
        "max_loss_streak": int(max_loss_streak),
        "hit_27pct_stop": int(equity.min() <= initial_equity * 0.73),
        "pnl_share_from_gt5x": float(gt5_profit / wins) if wins > 0 else 0.0,
        "near_liq_trades": int(trades.get("near_liq_flag", pd.Series(dtype=bool)).fillna(False).sum()),
        "gt5x_trade_pct": float(gt5_trades),
        "status": "passed" if len(trades) > 0 else "failed_no_trades",
    }
