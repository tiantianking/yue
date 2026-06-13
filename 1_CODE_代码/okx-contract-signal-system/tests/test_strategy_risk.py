import pandas as pd

from okx_signal_system.risk.model import (
    COST_BUFFER_RATE,
    Ledger,
    RiskConfig,
    apply_halt_policy,
    smart_leverage_for_signal,
    validate_signal,
)
from okx_signal_system.strategy.trend_breakout import StrategyParams, TradeSignal, build_signal


def base_row(**overrides):
    row = {
        "ts": pd.Timestamp("2026-01-01T00:00:00Z"),
        "close": 110.0,
        "atr": 2.0,
        "bias_4h": "long",
        "breakout_high": 100.0,
        "breakout_low": 90.0,
        # 趋势强度检查需要 EMA 字段（间距 > 0.5% 才算强趋势）
        "ema_fast": 112.0,   # 快线在价格上方
        "ema_slow": 109.0,   # 慢线在价格下方
    }
    row.update(overrides)
    return pd.Series(row)


def test_long_breakout_signal_has_required_protection() -> None:
    signal = build_signal(base_row(), inst_id="BTC-USDT-SWAP", params=StrategyParams())
    assert signal.accepted
    assert signal.entry_ref == 110.0
    assert signal.stop_loss is not None
    assert signal.take_profit is not None
    assert signal.max_hold_bars == 96
    assert signal.signal_score is not None and signal.signal_score >= 6
    assert signal.risk_reward_ratio == 4.0


def test_rejects_without_breakout() -> None:
    signal = build_signal(base_row(close=95.0), inst_id="BTC-USDT-SWAP")
    assert not signal.accepted
    assert signal.reject_reason == "no_breakout"


def test_strategy_rejects_target_rr_below_3_5() -> None:
    signal = build_signal(
        base_row(),
        inst_id="BTC-USDT-SWAP",
        params=StrategyParams(take_profit_mult=3.0),
    )
    assert not signal.accepted
    assert signal.reject_reason == "risk_reward_too_low"


def test_halt_policy_stops_new_entries_at_27_percent_loss() -> None:
    ledger = Ledger("BTC-USDT-SWAP", init_capital=10000, equity=7300)
    halted = apply_halt_policy(ledger, RiskConfig())
    assert halted.status == "halted"


def test_risk_accepts_protected_signal_and_caps_leverage() -> None:
    signal = build_signal(base_row(), inst_id="BTC-USDT-SWAP")
    decision = validate_signal(signal, Ledger("BTC-USDT-SWAP", init_capital=10000, equity=10000))
    assert decision.accepted
    assert decision.margin_mode == "isolated"
    assert decision.position_mode == "one_way"
    assert decision.leverage_cap <= 10
    assert decision.leverage_used and 1 <= decision.leverage_used <= 10
    assert decision.qty and decision.qty > 0
    assert decision.margin_loss_pct is not None
    assert decision.margin_loss_pct <= RiskConfig().single_position_loss_pct + 1e-12


def test_risk_caps_margin_loss_at_stop_to_27_percent() -> None:
    signal = TradeSignal(
        ts=pd.Timestamp("2026-01-01T00:00:00Z"),
        inst_id="BTC-USDT-SWAP",
        side="long",
        entry_ref=100.0,
        stop_loss=75.0,
        take_profit=187.5,
        max_hold_bars=24,
        reason_codes=("TEST",),
        signal_score=9.5,
        risk_reward_ratio=3.5,
    )
    config = RiskConfig(max_leverage=10)
    decision = validate_signal(signal, Ledger("BTC-USDT-SWAP", init_capital=10000, equity=10000), config)
    max_leverage_for_margin_cap = config.single_position_loss_pct / (0.25 + COST_BUFFER_RATE)
    assert decision.accepted
    assert decision.leverage_used is not None
    assert decision.leverage_used <= max_leverage_for_margin_cap
    assert decision.margin_loss_pct is not None
    assert decision.margin_loss_pct <= config.single_position_loss_pct + 1e-12


def test_risk_rejects_open_position() -> None:
    signal = build_signal(base_row(), inst_id="BTC-USDT-SWAP")
    decision = validate_signal(signal, Ledger("BTC-USDT-SWAP", init_capital=10000, equity=10000, open_positions=1))
    assert not decision.accepted
    assert decision.reason == "position_open"


def test_strategy_rejects_too_close_protection_after_costs() -> None:
    signal = build_signal(base_row(atr=0.05, atr_pct=0.00045), inst_id="BTC-USDT-SWAP")
    assert not signal.accepted
    assert signal.reject_reason in {"atr_pct_too_low", "stop_distance_too_close"}


def test_smart_leverage_uses_signal_score_not_default_ten() -> None:
    weak = build_signal(base_row(atr=0.35, atr_pct=0.0032), inst_id="BTC-USDT-SWAP")
    strong = build_signal(base_row(close=118.0, breakout_high=100.0, atr=0.35, atr_pct=0.0030, vol_ratio=2.0), inst_id="BTC-USDT-SWAP")
    weak = weak.__class__(**{**weak.__dict__, "signal_score": 6.1})
    strong = strong.__class__(**{**strong.__dict__, "signal_score": 9.4})
    ledger = Ledger("BTC-USDT-SWAP", init_capital=10000, equity=10000)
    weak_lev = smart_leverage_for_signal(weak, ledger, RiskConfig(max_leverage=10))
    strong_lev = smart_leverage_for_signal(strong, ledger, RiskConfig(max_leverage=10))
    assert weak_lev < strong_lev <= 10
    assert weak_lev != 10
