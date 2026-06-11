import pandas as pd

from okx_signal_system.risk.model import Ledger, RiskConfig, apply_halt_policy, validate_signal
from okx_signal_system.strategy.trend_breakout import StrategyParams, build_signal


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
    assert signal.max_hold_bars == 48


def test_rejects_without_breakout() -> None:
    signal = build_signal(base_row(close=95.0), inst_id="BTC-USDT-SWAP")
    assert not signal.accepted
    assert signal.reject_reason == "no_breakout"


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
    assert decision.qty and decision.qty > 0


def test_risk_rejects_open_position() -> None:
    signal = build_signal(base_row(), inst_id="BTC-USDT-SWAP")
    decision = validate_signal(signal, Ledger("BTC-USDT-SWAP", init_capital=10000, equity=10000, open_positions=1))
    assert not decision.accepted
    assert decision.reason == "position_open"
