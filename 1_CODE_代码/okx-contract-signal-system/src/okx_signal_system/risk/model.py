from __future__ import annotations

from dataclasses import dataclass, replace

from okx_signal_system.strategy.trend_breakout import TradeSignal


@dataclass(frozen=True)
class RiskConfig:
    initial_equity: float = 10000.0
    halt_equity_ratio: float = 0.73
    max_leverage: float = 10.0
    risk_pct: float = 0.005
    margin_mode: str = "isolated"
    position_mode: str = "net_mode"


@dataclass(frozen=True)
class Ledger:
    inst_id: str
    init_capital: float
    equity: float
    open_positions: int = 0
    status: str = "active"
    loss_streak: int = 0
    max_drawdown: float = 0.0

    @property
    def allow_new_entry(self) -> bool:
        return self.status == "active" and self.open_positions == 0


@dataclass(frozen=True)
class RiskDecision:
    accepted: bool
    reason: str | None
    leverage_cap: float
    qty: float | None
    risk_amount: float | None
    margin_mode: str = "isolated"
    position_mode: str = "net_mode"


def apply_halt_policy(ledger: Ledger, config: RiskConfig) -> Ledger:
    if ledger.equity <= ledger.init_capital * config.halt_equity_ratio:
        return replace(ledger, status="halted")
    return ledger


def leverage_cap_for_signal(signal: TradeSignal, ledger: Ledger, config: RiskConfig) -> float:
    if not signal.accepted or signal.entry_ref is None or signal.stop_loss is None:
        return 0.0
    stop_pct = abs(signal.entry_ref - signal.stop_loss) / signal.entry_ref
    cap = config.max_leverage
    if stop_pct > 0.018:
        cap = min(cap, 2.0)
    elif stop_pct > 0.012:
        cap = min(cap, 5.0)
    if ledger.loss_streak >= 2:
        cap = min(cap, 5.0)
    if ledger.max_drawdown > 0.08:
        cap = min(cap, 5.0)
    return cap


def validate_signal(signal: TradeSignal, ledger: Ledger, config: RiskConfig = RiskConfig()) -> RiskDecision:
    active_ledger = apply_halt_policy(ledger, config)
    if config.margin_mode != "isolated":
        return RiskDecision(False, "margin_mode_not_isolated", 0.0, None, None)
    if config.position_mode != "net_mode":
        return RiskDecision(False, "position_mode_not_net", 0.0, None, None)
    if not signal.accepted:
        return RiskDecision(False, signal.reject_reason or "signal_rejected", 0.0, None, None)
    if not active_ledger.allow_new_entry:
        return RiskDecision(False, "ledger_not_allowed", 0.0, None, None)
    if signal.entry_ref is None or signal.stop_loss is None or signal.take_profit is None or signal.max_hold_bars is None:
        return RiskDecision(False, "missing_trade_protection", 0.0, None, None)
    leverage_cap = leverage_cap_for_signal(signal, active_ledger, config)
    if leverage_cap <= 0 or leverage_cap > config.max_leverage:
        return RiskDecision(False, "invalid_leverage", 0.0, None, None)
    per_unit_risk = abs(signal.entry_ref - signal.stop_loss)
    if per_unit_risk <= 0:
        return RiskDecision(False, "invalid_stop_distance", leverage_cap, None, None)
    risk_amount = active_ledger.equity * config.risk_pct
    qty = risk_amount / per_unit_risk
    if qty <= 0:
        return RiskDecision(False, "invalid_qty", leverage_cap, None, risk_amount)
    return RiskDecision(True, None, leverage_cap, qty, risk_amount)
