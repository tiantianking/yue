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


def continuation_frame(*, close: float = 111.4) -> pd.DataFrame:
    rows = []
    for idx in range(10):
        rows.append(
            {
                "ts": pd.Timestamp("2026-01-01T00:00:00Z") + pd.Timedelta(minutes=15 * idx),
                "open": 106.0 + idx * 0.2,
                "high": 108.0 + idx * 0.3,
                "low": 107.0,
                "close": 107.0 + idx * 0.2,
                "atr": 2.0,
                "atr_pct": 0.018,
                "vol_ratio": 2.2,
                "market_regime": "high_vol_trend",
                "trend_bias": "long",
                "breakout_high": 120.0,
                "breakout_low": 90.0,
                "ema_fast": 109.0,
                "ema_slow": 104.0,
                "signal_timeframe": "15m",
                "trend_timeframe": "1h",
            }
        )
    rows[-2]["close"] = 107.5
    rows[-1].update({"open": 108.0, "high": max(close, 110.5), "low": 108.6, "close": close})
    return pd.DataFrame(rows)


def test_long_breakout_signal_has_required_protection() -> None:
    signal = build_signal(base_row(), inst_id="BTC-USDT-SWAP", params=StrategyParams())
    assert signal.accepted
    assert signal.entry_ref == 110.0
    assert signal.stop_loss is not None
    assert signal.take_profit is not None
    assert signal.max_hold_bars == 768
    assert signal.signal_score is not None and signal.signal_score >= 6
    assert signal.risk_reward_ratio == 6.0


def test_long_pullback_continuation_signal_has_required_protection() -> None:
    frame = continuation_frame()
    signal = build_signal(
        frame.iloc[-1],
        inst_id="BTC-USDT-SWAP",
        params=StrategyParams(),
        frame=frame,
        idx=len(frame) - 1,
    )
    assert signal.accepted
    assert signal.side == "long"
    assert "15M_PULLBACK_RECLAIM_UP" in signal.reason_codes
    assert signal.risk_reward_ratio == 6.0
    assert signal.stop_loss == signal.entry_ref - 8.0
    assert signal.take_profit == signal.entry_ref + 48.0


def test_pullback_continuation_rejects_overextended_entry() -> None:
    frame = continuation_frame(close=116.0)
    signal = build_signal(
        frame.iloc[-1],
        inst_id="BTC-USDT-SWAP",
        params=StrategyParams(),
        frame=frame,
        idx=len(frame) - 1,
    )
    assert not signal.accepted
    assert signal.reject_reason == "no_breakout"


def test_rejects_without_breakout() -> None:
    signal = build_signal(base_row(close=95.0), inst_id="BTC-USDT-SWAP")
    assert not signal.accepted
    assert signal.reject_reason == "no_breakout"


def test_rejects_trend_strength_against_bias() -> None:
    signal = build_signal(
        base_row(ema_fast=108.0, ema_slow=112.0),
        inst_id="BTC-USDT-SWAP",
    )
    assert not signal.accepted
    assert signal.reject_reason == "trend_strength_wrong_direction"


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
    assert decision.leverage_cap == 0
    assert decision.leverage_used is None
    assert decision.qty is None
    assert decision.risk_amount is None
    assert decision.margin_loss_pct is None
    assert decision.risk_reward_ratio == 6.0


def test_signal_risk_ignores_account_margin_loss_model() -> None:
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
    assert decision.accepted
    assert decision.leverage_used is None
    assert decision.margin_loss_pct is None


def test_signal_risk_does_not_reject_existing_account_position() -> None:
    signal = build_signal(base_row(), inst_id="BTC-USDT-SWAP")
    decision = validate_signal(signal, Ledger("BTC-USDT-SWAP", init_capital=10000, equity=10000, open_positions=1))
    assert decision.accepted
    assert decision.reason is None


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
