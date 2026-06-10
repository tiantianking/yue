from __future__ import annotations

from dataclasses import asdict, dataclass

import pandas as pd

from okx_signal_system.features.indicators import build_feature_frame
from okx_signal_system.risk.costs import estimate_costs, participation_rate, slippage_bps_for_participation
from okx_signal_system.risk.model import Ledger, RiskConfig, validate_signal
from okx_signal_system.strategy.trend_breakout import StrategyParams, build_signal


@dataclass(frozen=True)
class TradeRecord:
    inst_id: str
    entry_time: str
    exit_time: str
    side: str
    entry_price: float
    exit_price: float
    qty: float
    gross_pnl: float
    costs: float
    net_pnl: float
    exit_reason: str
    leverage_cap: float


def split_train_valid(frame: pd.DataFrame, *, valid_fraction: float = 0.25) -> tuple[pd.DataFrame, pd.DataFrame]:
    if not 0 < valid_fraction < 1:
        raise ValueError("valid_fraction must be between 0 and 1")
    split_at = int(len(frame) * (1 - valid_fraction))
    return frame.iloc[:split_at].reset_index(drop=True), frame.iloc[split_at:].reset_index(drop=True)


def exit_trade(features: pd.DataFrame, entry_idx: int, signal, params: StrategyParams) -> tuple[int, float, str]:
    end_idx = min(entry_idx + params.max_hold_bars, len(features) - 1)
    for idx in range(entry_idx + 1, end_idx + 1):
        row = features.iloc[idx]
        if signal.side == "long":
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
    ledger = Ledger(inst_id, init_capital=risk_config.initial_equity, equity=risk_config.initial_equity)
    trades: list[TradeRecord] = []
    idx = 0
    while idx < len(features) - 2:
        row = features.iloc[idx]
        signal = build_signal(row, inst_id=inst_id, params=params)
        decision = validate_signal(signal, ledger, risk_config)
        if not decision.accepted or decision.qty is None:
            idx += 1
            continue
        entry_idx = idx + 1
        entry_price = float(features.iloc[entry_idx]["open"])
        notional = abs(entry_price * decision.qty)
        try:
            slip_bps = slippage_bps_for_participation(
                participation_rate(notional=notional, close=entry_price, volume=float(features.iloc[entry_idx]["volume"]))
            )
        except ValueError:
            idx += 1
            continue
        exit_idx, exit_price, exit_reason = exit_trade(features, entry_idx, signal, params)
        side_mult = 1 if signal.side == "long" else -1
        gross_pnl = (exit_price - entry_price) * decision.qty * side_mult
        costs = estimate_costs(
            entry_price=entry_price,
            exit_price=exit_price,
            qty=decision.qty,
            entry_time=features.iloc[entry_idx]["ts"],
            exit_time=features.iloc[exit_idx]["ts"],
            slippage_bps=slip_bps,
        )
        net_pnl = gross_pnl - costs.total
        ledger = Ledger(
            inst_id=inst_id,
            init_capital=ledger.init_capital,
            equity=ledger.equity + net_pnl,
            loss_streak=ledger.loss_streak + 1 if net_pnl < 0 else 0,
            max_drawdown=max(ledger.max_drawdown, max(0.0, (ledger.init_capital - (ledger.equity + net_pnl)) / ledger.init_capital)),
        )
        trades.append(
            TradeRecord(
                inst_id=inst_id,
                entry_time=pd.Timestamp(features.iloc[entry_idx]["ts"]).isoformat(),
                exit_time=pd.Timestamp(features.iloc[exit_idx]["ts"]).isoformat(),
                side=signal.side,
                entry_price=entry_price,
                exit_price=exit_price,
                qty=float(decision.qty),
                gross_pnl=float(gross_pnl),
                costs=float(costs.total),
                net_pnl=float(net_pnl),
                exit_reason=exit_reason,
                leverage_cap=float(decision.leverage_cap),
            )
        )
        idx = max(exit_idx + 1, idx + 1)
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
        "status": "passed" if len(trades) > 0 else "failed_no_trades",
    }
