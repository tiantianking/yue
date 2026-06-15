from __future__ import annotations

from dataclasses import dataclass, replace

from okx_signal_system.strategy.trend_breakout import TradeSignal

COST_BUFFER_RATE = 0.002
LIQ_SAFETY_MARGIN = 1.5
VOL_RATIO_MIN = 0.5
COOL_OFF_BARS = 4
EXTREME_VOLATILITY_THRESHOLD = 3.0
RR_EPSILON = 1e-9


@dataclass(frozen=True)
class RiskConfig:
    initial_equity: float = 10000.0
    halt_equity_ratio: float = 0.73
    max_leverage: float = 10.0
    single_position_loss_pct: float = 0.27
    risk_per_trade_pct: float = 0.01
    margin_mode: str = "isolated"
    position_mode: str = "one_way"
    maintenance_margin_rate: float = 0.005
    liquidation_cost_buffer_pct: float = 0.002
    min_stop_distance_pct: float = 0.004
    min_take_profit_distance_pct: float = 0.008
    min_reward_to_risk: float = 3.5
    min_signal_score: float = 6.0


@dataclass(frozen=True)
class Ledger:
    inst_id: str
    init_capital: float
    equity: float
    open_positions: int = 0
    status: str = "active"
    loss_streak: int = 0
    max_drawdown: float = 0.0
    cool_off_bars: int = 0
    peak_equity: float | None = None

    @property
    def allow_new_entry(self) -> bool:
        return self.status == "active" and self.open_positions == 0 and self.cool_off_bars <= 0


@dataclass(frozen=True)
class RiskDecision:
    accepted: bool
    reason: str | None
    leverage_cap: float
    qty: float | None
    risk_amount: float | None
    margin_mode: str = "isolated"
    position_mode: str = "one_way"
    stop_distance_pct: float | None = None
    notional: float | None = None
    leverage_used: float | None = None
    est_liq_buffer_pct: float | None = None
    near_liq_flag: bool = False
    cost_buffer_pct: float = COST_BUFFER_RATE
    signal_score: float | None = None
    risk_reward_ratio: float | None = None
    stop_reason: str | None = None
    tp_reason: str | None = None
    max_position_loss_pct: float | None = None
    position_margin_loss_pct: float | None = None

    @property
    def max_loss_pct(self) -> float | None:
        return self.max_position_loss_pct

    @property
    def margin_loss_pct(self) -> float | None:
        return self.position_margin_loss_pct


def apply_halt_policy(ledger: Ledger, config: RiskConfig) -> Ledger:
    if ledger.equity <= ledger.init_capital * config.halt_equity_ratio:
        return replace(ledger, status="halted")
    return ledger


def _signal_score(signal: TradeSignal) -> float:
    value = signal.signal_score
    if value is None:
        return 5.0
    try:
        return float(max(1.0, min(10.0, value)))
    except (TypeError, ValueError):
        return 5.0


def _protection_metrics(signal: TradeSignal) -> tuple[float, float, float]:
    if signal.entry_ref is None or signal.stop_loss is None or signal.take_profit is None:
        return 0.0, 0.0, 0.0
    entry = float(signal.entry_ref)
    stop_dist = abs(entry - float(signal.stop_loss))
    take_dist = abs(float(signal.take_profit) - entry)
    rr = take_dist / stop_dist if stop_dist > 0 else 0.0
    return stop_dist, take_dist, rr


def leverage_cap_for_signal(signal: TradeSignal, ledger: Ledger, config: RiskConfig) -> float:
    if not signal.accepted or signal.entry_ref is None or signal.stop_loss is None:
        return 0.0

    max_leverage = min(float(config.max_leverage), 10.0)
    stop_pct = abs(signal.entry_ref - signal.stop_loss) / signal.entry_ref
    cap = max_leverage

    if stop_pct > 0.018:
        cap = min(cap, 2.0)
    elif stop_pct > 0.012:
        cap = min(cap, 5.0)
    cost_buffered_stop_pct = stop_pct + COST_BUFFER_RATE
    if cost_buffered_stop_pct > 0:
        cap = min(cap, config.single_position_loss_pct / cost_buffered_stop_pct)

    score = _signal_score(signal)
    if score < 6.0:
        cap = min(cap, 1.0)
    elif score < 7.0:
        cap = min(cap, 3.0)

    if ledger.loss_streak >= 2:
        cap = min(cap, 5.0)
    if ledger.max_drawdown > 0.08:
        cap = min(cap, 5.0)

    if cap < 1.0:
        return 0.0
    return float(max(1.0, min(cap, max_leverage)))


def smart_leverage_for_signal(signal: TradeSignal, ledger: Ledger, config: RiskConfig) -> float:
    cap = leverage_cap_for_signal(signal, ledger, config)
    if cap <= 0:
        return 0.0

    score = _signal_score(signal)
    if score >= 9.2:
        target = 10.0
    elif score >= 8.5:
        target = 7.0
    elif score >= 7.5:
        target = 5.0
    elif score >= 6.5:
        target = 3.0
    elif score >= 6.0:
        target = 2.0
    else:
        target = 1.0

    stop_dist, _take_dist, rr = _protection_metrics(signal)
    stop_pct = stop_dist / signal.entry_ref if signal.entry_ref else 0.0
    if stop_pct > 0.018:
        target = min(target, 2.0)
    elif stop_pct > 0.012:
        target = min(target, 5.0)
    if rr + RR_EPSILON < config.min_reward_to_risk:
        target = min(target, 1.0)
    elif rr < 2.0:
        target = min(target, 3.0)

    return float(max(1.0, min(target, cap, config.max_leverage, 10.0)))


def estimated_liquidation_buffer_pct(leverage_used: float, config: RiskConfig = RiskConfig()) -> float:
    if leverage_used <= 1:
        return 1.0
    return max(0.0, (1.0 / leverage_used) - config.maintenance_margin_rate - config.liquidation_cost_buffer_pct)


def _reject(
    reason: str,
    *,
    leverage_cap: float = 0.0,
    risk_amount: float | None = None,
    signal: TradeSignal | None = None,
    stop_distance_pct: float | None = None,
    notional: float | None = None,
    leverage_used: float | None = None,
    est_liq_buffer_pct: float | None = None,
    near_liq_flag: bool = False,
    position_margin_loss_pct: float | None = None,
) -> RiskDecision:
    return RiskDecision(
        accepted=False,
        reason=reason,
        leverage_cap=leverage_cap,
        qty=None,
        risk_amount=risk_amount,
        stop_distance_pct=stop_distance_pct,
        notional=notional,
        leverage_used=leverage_used,
        est_liq_buffer_pct=est_liq_buffer_pct,
        near_liq_flag=near_liq_flag,
        signal_score=_signal_score(signal) if signal else None,
        risk_reward_ratio=signal.risk_reward_ratio if signal else None,
        stop_reason=signal.stop_reason if signal else None,
        tp_reason=signal.tp_reason if signal else None,
        max_position_loss_pct=None,
        position_margin_loss_pct=position_margin_loss_pct,
    )


def validate_signal(signal: TradeSignal, ledger: Ledger, config: RiskConfig = RiskConfig()) -> RiskDecision:
    active_ledger = apply_halt_policy(ledger, config)

    if config.margin_mode != "isolated":
        return _reject("margin_mode_not_isolated", signal=signal)
    if config.position_mode not in {"one_way", "net_mode"}:
        return _reject("position_mode_not_one_way_or_net", signal=signal)
    if not signal.accepted:
        return _reject(signal.reject_reason or "signal_rejected", signal=signal)
    if not active_ledger.allow_new_entry:
        if active_ledger.status == "halted":
            reason = "ledger_halted"
        elif active_ledger.open_positions > 0:
            reason = "position_open"
        else:
            reason = "cool_off_active"
        return _reject(reason, signal=signal)
    if signal.entry_ref is None or signal.stop_loss is None or signal.take_profit is None or signal.max_hold_bars is None:
        return _reject("missing_trade_protection", signal=signal)
    if _signal_score(signal) < config.min_signal_score:
        return _reject("signal_score_below_threshold", signal=signal)

    entry = float(signal.entry_ref)
    stop_loss = float(signal.stop_loss)
    take_profit = float(signal.take_profit)
    if entry <= 0:
        return _reject("invalid_entry_price", signal=signal)
    if signal.side == "long" and not (stop_loss < entry < take_profit):
        return _reject("invalid_long_protection", signal=signal)
    if signal.side == "short" and not (take_profit < entry < stop_loss):
        return _reject("invalid_short_protection", signal=signal)

    stop_dist, take_dist, rr = _protection_metrics(signal)
    stop_pct = stop_dist / entry
    take_pct = take_dist / entry
    min_stop = max(config.min_stop_distance_pct, COST_BUFFER_RATE * 2.0)
    min_take = max(config.min_take_profit_distance_pct, min_stop * config.min_reward_to_risk)
    if stop_pct < min_stop:
        return _reject("stop_too_close_after_costs", signal=signal, stop_distance_pct=stop_pct)
    if take_pct < min_take:
        return _reject("take_profit_too_close_after_costs", signal=signal, stop_distance_pct=stop_pct)
    if rr + RR_EPSILON < config.min_reward_to_risk:
        return _reject("risk_reward_too_low", signal=signal, stop_distance_pct=stop_pct)

    leverage_cap = leverage_cap_for_signal(signal, active_ledger, config)
    if leverage_cap <= 0 or leverage_cap > config.max_leverage or leverage_cap > 10.0:
        return _reject("invalid_leverage", signal=signal)

    leverage_used = smart_leverage_for_signal(signal, active_ledger, config)
    if leverage_used <= 0:
        return _reject("invalid_leverage", leverage_cap=leverage_cap, signal=signal)

    cost_buffered_risk = stop_dist + entry * COST_BUFFER_RATE
    if cost_buffered_risk <= 0:
        return _reject("invalid_stop_distance", leverage_cap=leverage_cap, signal=signal)

    target_risk_pct = max(0.0, min(config.risk_per_trade_pct, config.single_position_loss_pct))
    risk_amount = active_ledger.equity * target_risk_pct
    qty = risk_amount / cost_buffered_risk
    if qty <= 0:
        return _reject("invalid_qty", leverage_cap=leverage_cap, risk_amount=risk_amount, signal=signal)

    max_notional = active_ledger.equity * leverage_used
    notional = qty * entry
    if notional > max_notional:
        qty = max_notional / entry
        notional = max_notional
        risk_amount = qty * cost_buffered_risk

    max_position_loss_pct = risk_amount / active_ledger.equity if active_ledger.equity > 0 else None
    liq_buffer_pct = estimated_liquidation_buffer_pct(leverage_used, config)
    position_margin_loss_pct = (stop_pct + COST_BUFFER_RATE) * leverage_used
    if position_margin_loss_pct > config.single_position_loss_pct + 1e-12:
        return _reject(
            "position_margin_loss_above_27pct",
            leverage_cap=leverage_cap,
            signal=signal,
            stop_distance_pct=stop_pct,
            notional=notional,
            leverage_used=leverage_used,
            position_margin_loss_pct=position_margin_loss_pct,
        )
    if liq_buffer_pct < stop_pct * LIQ_SAFETY_MARGIN:
        return _reject(
            "near_liquidation_before_stop",
            leverage_cap=leverage_cap,
            signal=signal,
            stop_distance_pct=stop_pct,
            notional=notional,
            leverage_used=leverage_used,
            est_liq_buffer_pct=liq_buffer_pct,
            near_liq_flag=True,
            position_margin_loss_pct=position_margin_loss_pct,
        )

    return RiskDecision(
        accepted=True,
        reason=None,
        leverage_cap=leverage_cap,
        qty=qty,
        risk_amount=risk_amount,
        stop_distance_pct=stop_pct,
        notional=notional,
        leverage_used=leverage_used,
        est_liq_buffer_pct=liq_buffer_pct,
        near_liq_flag=False,
        cost_buffer_pct=COST_BUFFER_RATE,
        signal_score=_signal_score(signal),
        risk_reward_ratio=rr,
        stop_reason=signal.stop_reason,
        tp_reason=signal.tp_reason,
        max_position_loss_pct=max_position_loss_pct,
        position_margin_loss_pct=position_margin_loss_pct,
    )
