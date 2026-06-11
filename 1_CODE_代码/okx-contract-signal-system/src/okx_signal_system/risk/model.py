from __future__ import annotations

from dataclasses import dataclass, replace

from okx_signal_system.strategy.trend_breakout import TradeSignal


# ============================================================
# 全局常量
# ============================================================

# 成本缓冲率（入场费 + 出场费 + 滑点 + 资金费估算）
COST_BUFFER_RATE = 0.002  # 0.2%

# 爆仓安全边际：liq_distance >= LIQ_SAFETY_MARGIN * stop_distance
LIQ_SAFETY_MARGIN = 1.5

# 成交量过滤阈值（vol_ratio < 0.5 时不开仓）
VOL_RATIO_MIN = 0.5

# 冷静期：连续极端波动后禁止开仓的 bar 数
COOL_OFF_BARS = 4

# 连续极端波动判断阈值（ATR 倍数）
EXTREME_VOLATILITY_THRESHOLD = 3.0


@dataclass(frozen=True)
class RiskConfig:
    initial_equity: float = 10000.0
    halt_equity_ratio: float = 0.73
    max_leverage: float = 10.0
    # 单币种最大亏损：本笔仓位的27%（不是总本金）
    single_position_loss_pct: float = 0.27
    margin_mode: str = "isolated"
    position_mode: str = "one_way"  # OKX 单向持仓模式
    maintenance_margin_rate: float = 0.005
    liquidation_cost_buffer_pct: float = 0.002


@dataclass(frozen=True)
class Ledger:
    inst_id: str
    init_capital: float
    equity: float
    open_positions: int = 0
    status: str = "active"
    loss_streak: int = 0
    max_drawdown: float = 0.0
    cool_off_bars: int = 0  # 冷静期剩余 bar 数

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


def apply_halt_policy(ledger: Ledger, config: RiskConfig) -> Ledger:
    if ledger.equity <= ledger.init_capital * config.halt_equity_ratio:
        return replace(ledger, status="halted")
    return ledger


def leverage_cap_for_signal(signal: TradeSignal, ledger: Ledger, config: RiskConfig) -> float:
    if not signal.accepted or signal.entry_ref is None or signal.stop_loss is None:
        return 0.0
    stop_pct = abs(signal.entry_ref - signal.stop_loss) / signal.entry_ref
    cap = config.max_leverage
    # ATR% 偏高时限制杠杆
    if stop_pct > 0.018:
        cap = min(cap, 2.0)
    elif stop_pct > 0.012:
        cap = min(cap, 5.0)
    # 亏损连击降杠杆
    if ledger.loss_streak >= 2:
        cap = min(cap, 5.0)
    # 回撤过大降杠杆
    if ledger.max_drawdown > 0.08:
        cap = min(cap, 5.0)
    # 最终杠杆不能超过配置的最大杠杆
    return min(cap, config.max_leverage)


def estimated_liquidation_buffer_pct(leverage_used: float, config: RiskConfig = RiskConfig()) -> float:
    """计算爆仓距离占总名义价值的百分比"""
    if leverage_used <= 1:
        return 1.0
    return max(0.0, (1.0 / leverage_used) - config.maintenance_margin_rate - config.liquidation_cost_buffer_pct)


def validate_signal(signal: TradeSignal, ledger: Ledger, config: RiskConfig = RiskConfig()) -> RiskDecision:
    """风控校验：检查信号是否满足所有风控约束"""
    active_ledger = apply_halt_policy(ledger, config)

    # 1. 保证金模式检查
    if config.margin_mode != "isolated":
        return RiskDecision(False, "margin_mode_not_isolated", 0.0, None, None)

    # 2. 持仓模式检查（支持 one_way 和 net_mode）
    if config.position_mode not in {"one_way", "net_mode"}:
        return RiskDecision(False, "position_mode_not_one_way_or_net", 0.0, None, None)

    # 3. 信号有效性检查
    if not signal.accepted:
        return RiskDecision(False, signal.reject_reason or "signal_rejected", 0.0, None, None)

    # 4. 账户状态检查
    if not active_ledger.allow_new_entry:
        if active_ledger.status == "halted":
            reason = "ledger_halted"
        elif active_ledger.open_positions > 0:
            reason = "position_open"
        else:
            reason = "cool_off_active"
        return RiskDecision(False, reason, 0.0, None, None)

    # 5. 必填字段检查
    if signal.entry_ref is None or signal.stop_loss is None or signal.take_profit is None or signal.max_hold_bars is None:
        return RiskDecision(False, "missing_trade_protection", 0.0, None, None)

    # 6. 杠杆上限检查
    leverage_cap = leverage_cap_for_signal(signal, active_ledger, config)
    if leverage_cap <= 0 or leverage_cap > config.max_leverage:
        return RiskDecision(False, "invalid_leverage", 0.0, None, None)

    # 7. 止损距离检查（包含成本缓冲）
    per_unit_risk = abs(signal.entry_ref - signal.stop_loss)
    # 成本缓冲后的真实止损距离
    cost_buffered_risk = per_unit_risk + signal.entry_ref * COST_BUFFER_RATE
    if cost_buffered_risk <= 0:
        return RiskDecision(False, "invalid_stop_distance", leverage_cap, None, None)

    # 8. 仓位计算：单币种最大亏损 = 本笔名义价值的27%
    # 第一步：计算本币种最大可开名义价值
    max_notional = active_ledger.equity * leverage_cap
    # 第二步：本笔最大亏损 = 最大名义价值 × 27%
    risk_amount = max_notional * config.single_position_loss_pct
    # 第三步：根据成本缓冲后的止损距离计算合约数量
    qty = risk_amount / cost_buffered_risk
    if qty <= 0:
        return RiskDecision(False, "invalid_qty", leverage_cap, None, risk_amount)
    # 第四步：计算名义价值
    notional = qty * signal.entry_ref

    # 9. 名义价值上限检查（确保不超过杠杆限制）
    if notional > max_notional:
        qty = max_notional / signal.entry_ref
        notional = max_notional
        risk_amount = qty * cost_buffered_risk

    # 10. 实际杠杆计算
    leverage_used = max(1.0, notional / active_ledger.equity)
    stop_distance_pct = per_unit_risk / signal.entry_ref
    liq_buffer_pct = estimated_liquidation_buffer_pct(leverage_used, config)

    # 11. 爆仓安全边际检查
    if liq_buffer_pct < stop_distance_pct * LIQ_SAFETY_MARGIN:
        return RiskDecision(
            False,
            "near_liquidation_before_stop",
            leverage_cap,
            None,
            None,
            stop_distance_pct=stop_distance_pct,
            notional=notional,
            leverage_used=leverage_used,
            est_liq_buffer_pct=liq_buffer_pct,
            near_liq_flag=True,
            cost_buffer_pct=COST_BUFFER_RATE,
        )

    return RiskDecision(
        True,
        None,
        leverage_cap,
        qty,
        risk_amount,
        stop_distance_pct=stop_distance_pct,
        notional=notional,
        leverage_used=leverage_used,
        est_liq_buffer_pct=liq_buffer_pct,
        near_liq_flag=False,
        cost_buffer_pct=COST_BUFFER_RATE,
    )