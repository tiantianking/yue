import numpy as np
import pandas as pd
import pytest

from okx_signal_system.backtest import runner
from okx_signal_system.risk.costs import CostConfig
from okx_signal_system.risk.model import SignalRiskAssessment
from okx_signal_system.strategy.ensemble import EnsembleResult
from okx_signal_system.strategy.trend_breakout import StrategyParams, TradeSignal


def test_signal_only_backtest_keeps_accepted_signal_without_exchange_qty(monkeypatch) -> None:
    features = pd.DataFrame(
        [
            {
                "ts": pd.Timestamp("2026-01-01T00:00:00Z") + pd.Timedelta(minutes=15 * idx),
                "open": 100.0 + idx,
                "high": 102.0 + idx,
                "low": 99.0 + idx,
                "close": 101.0 + idx,
                "volume": 1_000_000.0,
                "quote_volume": 100_000_000.0,
                "atr": 1.0,
                "atr_pct": 0.01,
                "vol_ratio": 2.0,
                "trend_bias": "long",
                "breakout_high": 100.0,
                "breakout_low": 90.0,
                "ema_fast": 110.0,
                "ema_slow": 100.0,
            }
            for idx in range(5)
        ]
    )

    monkeypatch.setattr(runner, "signal_candidate_indices", lambda _features: np.array([1]))

    def fake_build_signal(row, *, inst_id, params, frame, idx):
        return TradeSignal(
            ts=pd.Timestamp(row["ts"]),
            inst_id=inst_id,
            side="long",
            entry_ref=100.0,
            stop_loss=95.0,
            take_profit=130.0,
            max_hold_bars=2,
            reason_codes=("TEST",),
            signal_score=8.0,
            risk_reward_ratio=6.0,
        )

    monkeypatch.setattr(runner, "build_signal", fake_build_signal)
    monkeypatch.setattr(
        runner,
        "ensemble_vote",
        lambda *args, **kwargs: EnsembleResult("long", 8.0, [], 1.0, "test"),
    )
    monkeypatch.setattr(runner, "vote_gate_passed", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        runner,
        "validate_signal",
        lambda *args, **kwargs: SignalRiskAssessment(
            accepted=True,
            reason=None,
            stop_distance_pct=5.0 / 102.0,
            signal_score=8.0,
            risk_reward_ratio=6.0,
        ),
    )

    trades = runner.run_backtest_from_features(
        features,
        inst_id="BTC-USDT-SWAP",
        params=StrategyParams(max_hold_bars=2),
    )

    assert len(trades) == 1
    trade = trades.iloc[0]
    assert trade["sizing_mode"] == "signal_only_research_risk"
    assert trade["qty"] > 0
    assert trade["risk_amount"] == 100.0
    assert trade["outcome"] in {"TP", "SL", "TIMEOUT"}
    assert {"net_r", "final_net_r"}.issubset(trades.columns)


def test_backtest_cooldown_advances_by_bar_index_not_candidate_count(monkeypatch) -> None:
    features = pd.DataFrame(
        [
            {
                "ts": pd.Timestamp("2026-01-01T00:00:00Z") + pd.Timedelta(hours=idx),
                "open": 100.0,
                "high": 102.0,
                "low": 99.0,
                "close": 101.0,
                "volume": 1_000_000.0,
                "quote_volume": 100_000_000.0,
                "atr": 1.0,
                "atr_pct": 0.01,
                "vol_ratio": 2.0,
                "trend_bias": "long",
                "breakout_high": 100.0,
                "breakout_low": 90.0,
                "ema_fast": 110.0,
                "ema_slow": 100.0,
            }
            for idx in range(10)
        ]
    )

    monkeypatch.setattr(runner, "signal_candidate_indices", lambda _features: np.array([1, 3, 6]))
    cool_mask = np.zeros(len(features), dtype=bool)
    cool_mask[1] = True
    monkeypatch.setattr(runner, "cool_off_condition_mask", lambda _features, _atr_window: cool_mask)
    build_calls = []

    def fake_build_signal(row, *, inst_id, params, frame, idx):
        build_calls.append(idx)
        return TradeSignal(
            ts=pd.Timestamp(row["ts"]),
            inst_id=inst_id,
            side="long",
            entry_ref=100.0,
            stop_loss=95.0,
            take_profit=120.0,
            max_hold_bars=1,
            reason_codes=("TEST",),
            signal_score=8.0,
            risk_reward_ratio=4.0,
        )

    monkeypatch.setattr(runner, "build_signal", fake_build_signal)
    monkeypatch.setattr(
        runner,
        "ensemble_vote",
        lambda *args, **kwargs: EnsembleResult("long", 8.0, [], 1.0, "test"),
    )
    monkeypatch.setattr(runner, "vote_gate_passed", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        runner,
        "validate_signal",
        lambda *args, **kwargs: SignalRiskAssessment(
            accepted=True,
            reason=None,
            stop_distance_pct=0.05,
            signal_score=8.0,
            risk_reward_ratio=4.0,
        ),
    )

    trades = runner.run_backtest_from_features(
        features,
        inst_id="BTC-USDT-SWAP",
        params=StrategyParams(max_hold_bars=1),
    )

    assert build_calls == [6]
    assert len(trades) == 1


def test_backtest_validation_rejects_unsupported_outcome() -> None:
    trades = pd.DataFrame([{column: 1 for column in runner.REQUIRED_BACKTEST_RESULT_COLUMNS}])
    trades["outcome"] = "TREND_REVERSE"

    with pytest.raises(ValueError, match="unsupported backtest outcomes: TREND_REVERSE"):
        runner.validate_backtest_result(trades, context="quality_model_training")


def test_backtest_drops_incomplete_tail_timeout(monkeypatch) -> None:
    features = pd.DataFrame(
        [
            {
                "ts": pd.Timestamp("2026-01-01T00:00:00Z") + pd.Timedelta(minutes=15 * idx),
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.5,
                "volume": 1_000_000.0,
                "quote_volume": 100_000_000.0,
                "atr": 1.0,
                "atr_pct": 0.01,
                "vol_ratio": 2.0,
                "trend_bias": "long",
                "breakout_high": 99.0,
                "breakout_low": 90.0,
                "ema_fast": 110.0,
                "ema_slow": 100.0,
                "is_closed": True,
            }
            for idx in range(4)
        ]
    )
    monkeypatch.setattr(runner, "signal_candidate_indices", lambda _features: np.array([1]))
    monkeypatch.setattr(
        runner,
        "build_signal",
        lambda row, *, inst_id, params, frame, idx: TradeSignal(
            ts=pd.Timestamp(row["ts"]),
            inst_id=inst_id,
            side="long",
            entry_ref=100.0,
            stop_loss=90.0,
            take_profit=120.0,
            max_hold_bars=5,
            reason_codes=("TEST",),
            signal_score=8.0,
            risk_reward_ratio=2.0,
        ),
    )
    monkeypatch.setattr(runner, "ensemble_vote", lambda *args, **kwargs: EnsembleResult("long", 8.0, [], 1.0, "test"))
    monkeypatch.setattr(runner, "vote_gate_passed", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        runner,
        "validate_signal",
        lambda *args, **kwargs: SignalRiskAssessment(
            accepted=True,
            reason=None,
            stop_distance_pct=0.1,
            signal_score=8.0,
            risk_reward_ratio=2.0,
        ),
    )

    trades = runner.run_backtest_from_features(
        features,
        inst_id="BTC-USDT-SWAP",
        params=StrategyParams(max_hold_bars=5),
    )

    assert trades.empty


def test_backtest_slippage_uses_supplied_cost_config(monkeypatch) -> None:
    features = pd.DataFrame(
        [
            {
                "ts": pd.Timestamp("2026-01-01T00:00:00Z") + pd.Timedelta(minutes=15 * idx),
                "open": 100.0,
                "high": 130.0,
                "low": 99.0,
                "close": 120.0,
                "volume": 1_000_000.0,
                "quote_volume": 100_000_000.0,
                "atr": 1.0,
                "atr_pct": 0.01,
                "vol_ratio": 2.0,
                "trend_bias": "long",
                "breakout_high": 99.0,
                "breakout_low": 90.0,
                "ema_fast": 110.0,
                "ema_slow": 100.0,
                "is_closed": True,
            }
            for idx in range(5)
        ]
    )
    monkeypatch.setattr(runner, "signal_candidate_indices", lambda _features: np.array([1]))
    monkeypatch.setattr(
        runner,
        "build_signal",
        lambda row, *, inst_id, params, frame, idx: TradeSignal(
            ts=pd.Timestamp(row["ts"]),
            inst_id=inst_id,
            side="long",
            entry_ref=100.0,
            stop_loss=95.0,
            take_profit=110.0,
            max_hold_bars=2,
            reason_codes=("TEST",),
            signal_score=8.0,
            risk_reward_ratio=2.0,
        ),
    )
    monkeypatch.setattr(runner, "ensemble_vote", lambda *args, **kwargs: EnsembleResult("long", 8.0, [], 1.0, "test"))
    monkeypatch.setattr(runner, "vote_gate_passed", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        runner,
        "validate_signal",
        lambda *args, **kwargs: SignalRiskAssessment(
            accepted=True,
            reason=None,
            stop_distance_pct=0.05,
            signal_score=8.0,
            risk_reward_ratio=2.0,
        ),
    )

    trades = runner.run_backtest_from_features(
        features,
        inst_id="BTC-USDT-SWAP",
        params=StrategyParams(max_hold_bars=2),
        cost_config=CostConfig(normal_slippage_bps=7.0),
    )

    trade = trades.iloc[0]
    expected_slippage = abs(trade["entry_price"] * trade["qty"]) * 0.0007 + abs(trade["exit_price"] * trade["qty"]) * 0.0007
    assert trade["slippage_cost"] == pytest.approx(expected_slippage)
