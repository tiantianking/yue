from __future__ import annotations

from dataclasses import asdict, dataclass, replace

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
from okx_signal_system.signal_quality.outcome import SignalOutcomeLevels, SignalOutcomeSimulator
from okx_signal_system.strategy.ensemble import ensemble_vote
from okx_signal_system.strategy.vote_gate import DEFAULT_MIN_VOTE_APPROVAL_RATE, vote_gate_passed
from okx_signal_system.strategy.trend_breakout import (
    ATR_PCT_MIN,
    CONTINUATION_TREND_STRENGTH_MIN,
    CONTINUATION_VOL_RATIO_MIN,
    MAX_EXTENSION_ATR,
    PULLBACK_ATR_BAND,
    PULLBACK_DEEP_ATR_LIMIT,
    PULLBACK_LOOKBACK_BARS,
    PULLBACK_RECLAIM_MIN_ATR,
    TREND_STRENGTH_MIN,
    VOL_RATIO_MIN,
    StrategyParams,
    build_signal,
)


def _trend_bias_series(features: pd.DataFrame) -> pd.Series:
    if "trend_bias" in features.columns:
        return features["trend_bias"].astype(str)
    return features["bias_4h"].astype(str)


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
    outcome: str
    net_r: float
    final_net_r: float
    leverage_cap: float
    leverage_used: float
    stop_distance_pct: float
    est_liq_buffer_pct: float
    near_liq_flag: bool
    sizing_mode: str


BACKTEST_RESULT_COLUMNS = tuple(TradeRecord.__dataclass_fields__)
SUPPORTED_BACKTEST_OUTCOMES = frozenset({"TP", "SL", "TIMEOUT"})
REQUIRED_BACKTEST_RESULT_COLUMNS = frozenset(
    {
        "inst_id",
        "entry_time",
        "exit_time",
        "side",
        "entry_price",
        "exit_price",
        "stop_loss",
        "take_profit",
        "qty",
        "risk_amount",
        "notional",
        "gross_pnl",
        "costs",
        "net_pnl",
        "exit_reason",
        "outcome",
        "net_r",
        "final_net_r",
    }
)


def empty_backtest_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=BACKTEST_RESULT_COLUMNS)


def validate_backtest_result(trades: pd.DataFrame | None, *, context: str = "backtest") -> pd.DataFrame:
    if trades is None:
        raise ValueError(f"{context} produced no backtest result")
    missing = sorted(REQUIRED_BACKTEST_RESULT_COLUMNS.difference(trades.columns))
    if missing:
        raise ValueError(f"{context} missing backtest columns: {', '.join(missing)}")
    if trades.empty:
        raise ValueError(f"{context} produced no backtest rows")
    unsupported = sorted(set(trades["outcome"].dropna().astype(str)).difference(SUPPORTED_BACKTEST_OUTCOMES))
    if unsupported:
        raise ValueError(f"{context} has unsupported backtest outcomes: {', '.join(unsupported)}")
    return trades


def _exit_reason_to_outcome(exit_reason: str) -> str:
    if exit_reason == "take_profit":
        return "TP"
    if exit_reason == "stop_loss":
        return "SL"
    return "TIMEOUT"


_OUTCOME_SIMULATOR = SignalOutcomeSimulator()


def _research_position_size(
    *,
    entry_price: float,
    stop_distance: float,
    risk_config: RiskConfig,
) -> tuple[float, float, float]:
    risk_unit = float(risk_config.initial_equity) * float(risk_config.risk_per_trade_pct)
    if entry_price <= 0 or stop_distance <= 0 or risk_unit <= 0:
        raise ValueError("invalid_research_position_size")
    qty = risk_unit / stop_distance
    notional = abs(entry_price * qty)
    return float(qty), float(risk_unit), float(notional)


def split_train_valid(frame: pd.DataFrame, *, valid_fraction: float = 0.25) -> tuple[pd.DataFrame, pd.DataFrame]:
    if not 0 < valid_fraction < 1:
        raise ValueError("valid_fraction must be between 0 and 1")
    split_at = int(len(frame) * (1 - valid_fraction))
    return frame.iloc[:split_at].reset_index(drop=True), frame.iloc[split_at:].reset_index(drop=True)


def signal_candidate_indices(features: pd.DataFrame) -> np.ndarray:
    if len(features) < 3:
        return np.array([], dtype=int)

    close = features["close"].to_numpy(dtype=float)
    atr_value = features["atr"].to_numpy(dtype=float)
    atr_pct = (
        features["atr_pct"].to_numpy(dtype=float)
        if "atr_pct" in features.columns
        else np.divide(atr_value, close, out=np.full(len(features), np.nan), where=close > 0)
    )
    bias = _trend_bias_series(features).to_numpy()
    breakout_high = features["breakout_high"].to_numpy(dtype=float)
    breakout_low = features["breakout_low"].to_numpy(dtype=float)
    vol_ratio = (
        features["vol_ratio"].to_numpy(dtype=float)
        if "vol_ratio" in features.columns
        else np.ones(len(features), dtype=float)
    )
    ema_fast = features["ema_fast"].to_numpy(dtype=float)
    ema_slow = features["ema_slow"].to_numpy(dtype=float)
    trend_strength = np.divide(
        ema_fast - ema_slow,
        close,
        out=np.zeros(len(features), dtype=float),
        where=close > 0,
    )

    long_breakout = (bias == "long") & (close > breakout_high)
    short_breakout = (bias == "short") & (close < breakout_low)
    open_ = features["open"].to_numpy(dtype=float)
    pullback_window = PULLBACK_LOOKBACK_BARS + 1
    recent_low = features["low"].rolling(pullback_window, min_periods=3).min().to_numpy(dtype=float)
    recent_high = features["high"].rolling(pullback_window, min_periods=3).max().to_numpy(dtype=float)
    prev_close = features["close"].shift(1).to_numpy(dtype=float)
    long_continuation = (
        (bias == "long")
        & (trend_strength >= CONTINUATION_TREND_STRENGTH_MIN)
        & (ema_fast > ema_slow)
        & (vol_ratio >= CONTINUATION_VOL_RATIO_MIN)
        & (close > ema_fast)
        & (close > open_)
        & (close > prev_close)
        & (recent_low <= ema_fast + atr_value * PULLBACK_ATR_BAND)
        & (recent_low >= ema_fast - atr_value * PULLBACK_DEEP_ATR_LIMIT)
        & ((close - ema_fast) >= atr_value * PULLBACK_RECLAIM_MIN_ATR)
        & ((close - ema_fast) <= atr_value * MAX_EXTENSION_ATR)
    )
    short_continuation = (
        (bias == "short")
        & (trend_strength <= -CONTINUATION_TREND_STRENGTH_MIN)
        & (ema_fast < ema_slow)
        & (vol_ratio >= CONTINUATION_VOL_RATIO_MIN)
        & (close < ema_fast)
        & (close < open_)
        & (close < prev_close)
        & (recent_high >= ema_fast - atr_value * PULLBACK_ATR_BAND)
        & (recent_high <= ema_fast + atr_value * PULLBACK_DEEP_ATR_LIMIT)
        & ((ema_fast - close) >= atr_value * PULLBACK_RECLAIM_MIN_ATR)
        & ((ema_fast - close) <= atr_value * MAX_EXTENSION_ATR)
    )
    indices = np.arange(len(features))
    candidate_mask = (
        (indices < len(features) - 2)
        & np.isfinite(close)
        & (close > 0)
        & np.isfinite(atr_value)
        & (atr_value > 0)
        & np.isfinite(atr_pct)
        & (atr_pct >= ATR_PCT_MIN)
        & np.isfinite(breakout_high)
        & np.isfinite(breakout_low)
        & np.isfinite(vol_ratio)
        & (vol_ratio >= VOL_RATIO_MIN)
        & (np.abs(trend_strength) >= TREND_STRENGTH_MIN)
        & (long_breakout | short_breakout | long_continuation | short_continuation)
    )
    return np.flatnonzero(candidate_mask)


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


def cool_off_condition_mask(features: pd.DataFrame, atr_window: int = 14) -> np.ndarray:
    if len(features) == 0 or "atr" not in features.columns:
        return np.zeros(len(features), dtype=bool)
    close = pd.to_numeric(features["close"], errors="coerce")
    atr_values = pd.to_numeric(features["atr"], errors="coerce")
    mean_close = close.shift(1).rolling(atr_window, min_periods=atr_window).mean()
    mean_atr = atr_values.shift(1).rolling(atr_window, min_periods=atr_window).mean()
    mean_atr_pct = mean_atr / mean_close.replace(0, np.nan)
    cur_atr_pct = atr_values / close.replace(0, np.nan)
    mask = cur_atr_pct > EXTREME_VOLATILITY_THRESHOLD * mean_atr_pct
    return mask.fillna(False).to_numpy(dtype=bool)


def exit_trade(features: pd.DataFrame, entry_idx: int, signal, params: StrategyParams) -> tuple[int, float, str]:
    result = _OUTCOME_SIMULATOR.simulate_signal(
        replace(signal, max_hold_bars=params.max_hold_bars),
        features,
        start_idx=entry_idx,
        after_signal_time=False,
        include_trend_reverse=True,
    )
    if result is None:
        end_idx = min(entry_idx + params.max_hold_bars, len(features) - 1)
        return end_idx, float(features.iloc[end_idx]["close"]), "max_hold"
    return result.exit_idx, result.exit_price, result.exit_reason


def exit_trade_from_arrays(
    *,
    high: np.ndarray,
    low: np.ndarray,
    open_: np.ndarray,
    close: np.ndarray,
    bias: np.ndarray,
    entry_idx: int,
    side: str,
    stop_loss: float,
    take_profit: float,
    max_hold_bars: int,
) -> tuple[int, float, str]:
    frame = pd.DataFrame(
        {
            "ts": pd.RangeIndex(len(open_)),
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "trend_bias": bias,
            "is_closed": True,
        }
    )
    stop_dist = abs(float(open_[entry_idx]) - float(stop_loss))
    if stop_dist <= 0:
        end_idx = min(entry_idx + max_hold_bars, len(open_) - 1)
        return end_idx, float(close[end_idx]), "max_hold"
    levels = SignalOutcomeLevels(
        entry_price=float(open_[entry_idx]),
        stop_loss=float(stop_loss),
        take_profit=float(take_profit),
        stop_dist=stop_dist,
        reward_to_risk=abs(float(take_profit) - float(open_[entry_idx])) / stop_dist,
    )
    result = _OUTCOME_SIMULATOR.simulate_levels(
        side=side,
        levels=levels,
        frame=frame,
        start_idx=entry_idx,
        max_hold_bars=max_hold_bars,
        include_trend_reverse=True,
    )
    if result is None:
        end_idx = min(entry_idx + max_hold_bars, len(open_) - 1)
        return end_idx, float(close[end_idx]), "max_hold"
    return result.exit_idx, result.exit_price, result.exit_reason


def run_backtest(
    frame_1h: pd.DataFrame,
    *,
    inst_id: str,
    params: StrategyParams = StrategyParams(),
    risk_config: RiskConfig = RiskConfig(),
    signal_timeframe: str = "1h",
    trend_timeframe: str | None = None,
    min_vote_approval_rate: float = DEFAULT_MIN_VOTE_APPROVAL_RATE,
) -> pd.DataFrame:
    features = build_feature_frame(
        frame_1h,
        fast_ema=params.fast_ema,
        slow_ema=params.slow_ema,
        breakout_window=params.breakout_window,
        atr_window=params.atr_window,
        signal_timeframe=signal_timeframe,
        trend_timeframe=trend_timeframe,
    ).reset_index(drop=True)
    return run_backtest_from_features(
        features,
        inst_id=inst_id,
        params=params,
        risk_config=risk_config,
        min_vote_approval_rate=min_vote_approval_rate,
    )


def run_backtest_from_features(
    features: pd.DataFrame,
    *,
    inst_id: str,
    params: StrategyParams = StrategyParams(),
    risk_config: RiskConfig = RiskConfig(),
    min_vote_approval_rate: float = DEFAULT_MIN_VOTE_APPROVAL_RATE,
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
        return empty_backtest_frame()

    ts = features["ts"].to_numpy()
    open_ = features["open"].to_numpy(dtype=float)
    volume = features["volume"].to_numpy(dtype=float)
    quote_volume = (
        features["quote_volume"].to_numpy(dtype=float)
        if "quote_volume" in features.columns
        else np.full(len(features), np.nan)
    )
    candidate_indices = signal_candidate_indices(features)
    cool_off_mask = cool_off_condition_mask(features, params.atr_window)

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
                peak_equity=ledger.peak_equity,
            )
            continue

        # 检测冷静期条件：最近3根bar有连续极端波动
        if bool(cool_off_mask[idx]):
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
                peak_equity=ledger.peak_equity,
            )
            continue

        signal = build_signal(features.iloc[idx], inst_id=inst_id, params=params, frame=features, idx=idx)
        entry_idx = idx + 1
        entry_price = float(open_[entry_idx])
        if not signal.accepted or signal.entry_ref is None or signal.stop_loss is None or signal.take_profit is None:
            continue
        vote = ensemble_vote(features.iloc[idx], params, features, idx, base_score=signal.signal_score or 5.0, base_signal=signal)
        if not vote_gate_passed(vote.final_side, signal.side, vote.approval_rate, min_vote_approval_rate):
            continue
        effective_score = signal.signal_score or 5.0
        if vote.final_side == "flat":
            effective_score = max(1.0, effective_score - 3.0)
        elif vote.final_side != signal.side:
            effective_score = max(1.0, effective_score - 1.5)
        else:
            effective_score = vote.final_score
        levels = _OUTCOME_SIMULATOR.levels_from_signal(signal, entry_price=entry_price)
        if levels is None:
            continue
        stop_dist = levels.stop_dist
        stop_loss = levels.stop_loss
        take_profit = levels.take_profit
        signal = replace(signal, entry_ref=entry_price, stop_loss=stop_loss, take_profit=take_profit, signal_score=effective_score)
        decision = validate_signal(signal, ledger, risk_config)
        if not decision.accepted:
            continue
        if decision.qty is None:
            try:
                qty, risk_unit, notional = _research_position_size(
                    entry_price=entry_price,
                    stop_distance=stop_dist,
                    risk_config=risk_config,
                )
            except ValueError:
                continue
            sizing_mode = "signal_only_research_risk"
        else:
            qty = float(decision.qty)
            risk_unit = float(decision.risk_amount or abs(stop_dist * qty))
            notional = float(decision.notional or abs(entry_price * qty))
            sizing_mode = "risk_decision_qty"
        try:
            slip_bps = slippage_bps_for_participation(
                participation_rate(
                    notional=notional,
                    close=entry_price,
                    volume=float(volume[entry_idx]),
                    quote_volume=float(quote_volume[entry_idx]),
                )
            )
        except ValueError:
            continue
        outcome = _OUTCOME_SIMULATOR.simulate_signal(
            signal,
            features,
            start_idx=entry_idx,
            after_signal_time=False,
            include_trend_reverse=True,
        )
        if outcome is None:
            continue
        exit_idx = outcome.exit_idx
        exit_price = outcome.exit_price
        exit_reason = outcome.exit_reason

        side_mult = 1 if signal.side == "long" else -1
        gross_pnl = (exit_price - entry_price) * qty * side_mult
        costs = estimate_costs(
            entry_price=entry_price,
            exit_price=exit_price,
            qty=qty,
            entry_time=pd.Timestamp(ts[entry_idx]),
            exit_time=pd.Timestamp(ts[exit_idx]),
            slippage_bps=slip_bps,
        )
        net_pnl = gross_pnl - costs.total
        net_r = net_pnl / risk_unit if risk_unit > 0 else 0.0

        # 更新ledger状态
        new_equity = ledger.equity + net_pnl
        new_loss_streak = ledger.loss_streak + 1 if net_pnl < 0 else 0
        peak_equity = max(float(ledger.peak_equity or ledger.init_capital), float(new_equity))
        current_drawdown = (peak_equity - float(new_equity)) / peak_equity if peak_equity > 0 else 0.0
        new_max_drawdown = max(
            ledger.max_drawdown,
            max(0.0, current_drawdown),
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
            peak_equity=peak_equity,
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
                qty=float(qty),
                risk_amount=float(risk_unit),
                notional=float(notional),
                gross_pnl=float(gross_pnl),
                costs=float(costs.total),
                net_pnl=float(net_pnl),
                exit_reason=exit_reason,
                leverage_cap=float(decision.leverage_cap),
                leverage_used=float(decision.leverage_used or 0.0),
                stop_distance_pct=float(decision.stop_distance_pct or 0.0),
                est_liq_buffer_pct=float(decision.est_liq_buffer_pct or 0.0),
                near_liq_flag=bool(decision.near_liq_flag),
                net_r=float(net_r),
                outcome=_exit_reason_to_outcome(exit_reason),
                final_net_r=float(net_r),
                sizing_mode=sizing_mode,
            )
        )
        cursor = max(exit_idx + 1, idx + 1)
    return pd.DataFrame([asdict(trade) for trade in trades], columns=BACKTEST_RESULT_COLUMNS)


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
    gt5_profit = trades[(trades["leverage_used"] > 5) & (trades["net_pnl"] > 0)]["net_pnl"].sum()
    gt5_trades = (trades["leverage_used"] > 5).mean()
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
