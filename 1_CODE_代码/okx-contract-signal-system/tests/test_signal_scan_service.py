from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pandas as pd

from okx_signal_system.risk.model import Ledger, RiskConfig
from okx_signal_system.signal_service.scan import SignalScanContext, SignalScanService
from okx_signal_system.strategy.ensemble import EnsembleResult
from okx_signal_system.strategy.trend_breakout import StrategyParams, TradeSignal


class FakeRegimeManager:
    def update_regime(self, _features):
        return "low_vol_trend", StrategyParams()

    def get_score_penalty(self) -> float:
        return 0.0

    def get_leverage_factor(self) -> float:
        return 1.0


@dataclass
class FakeShadowScore:
    def as_dict(self):
        return {"enabled": False, "reason": "test"}


class FakeQualityShadow:
    def score(self, _signal, _features):
        return FakeShadowScore()


class FakeLifecycleStore:
    def __init__(self):
        self.recorded = []

    def update_symbol(self, _inst_id, _frame):
        return 0

    def record_signal(self, signal, **kwargs):
        self.recorded.append((signal, kwargs))
        return None


class FakeShadowLedger:
    def __init__(self):
        self.min_closed_values = []

    def update_symbol(self, _inst_id, _frame):
        return 0

    def score_adjustment(self, _inst_id, _side, *, min_closed=6):
        self.min_closed_values.append(min_closed)
        return 0.0


def _frame() -> pd.DataFrame:
    ts = pd.date_range("2026-01-01T00:00:00Z", periods=12, freq="15min")
    return pd.DataFrame(
        {
            "ts": ts,
            "open": [100.0] * len(ts),
            "high": [101.0] * len(ts),
            "low": [99.0] * len(ts),
            "close": [100.0] * len(ts),
            "volume": [1000.0] * len(ts),
            "is_closed": [True] * len(ts),
        }
    )


def test_signal_scan_service_returns_ranked_ready_candidate(monkeypatch) -> None:
    async def loader(_inst_id: str, _limit: int) -> pd.DataFrame:
        return _frame()

    def fake_build_feature_frame(frame, **_kwargs):
        out = frame.copy()
        out["atr"] = 2.0
        out["atr_pct"] = 0.02
        out["breakout_high"] = 99.0
        out["breakout_low"] = 95.0
        out["trend_bias"] = "long"
        out["ema_fast"] = 101.0
        out["ema_slow"] = 99.0
        out["vol_ratio"] = 1.2
        out["signal_timeframe"] = "15m"
        out["trend_timeframe"] = "1h"
        return out

    def fake_build_signal(row, *, inst_id, params, frame, idx):
        return TradeSignal(
            ts=row["ts"],
            inst_id=inst_id,
            side="long",
            entry_ref=100.0,
            stop_loss=92.0,
            take_profit=148.0,
            max_hold_bars=params.max_hold_bars,
            reason_codes=("TEST",),
            signal_score=7.0,
            risk_reward_ratio=6.0,
        )

    monkeypatch.setattr("okx_signal_system.signal_service.scan.build_feature_frame", fake_build_feature_frame)
    monkeypatch.setattr("okx_signal_system.signal_service.scan.build_signal", fake_build_signal)
    monkeypatch.setattr(
        "okx_signal_system.signal_service.scan.ensemble_vote",
        lambda *args, **kwargs: EnsembleResult("long", 8.0, [], 1.0, "test"),
    )

    lifecycle = FakeLifecycleStore()
    shadow_ledger = FakeShadowLedger()
    service = SignalScanService(
        candle_loader=loader,
        regime_manager=FakeRegimeManager(),
        quality_model_shadow=FakeQualityShadow(),
        lifecycle_store=lifecycle,
        shadow_ledger=shadow_ledger,
        notify_key_builder=lambda signal: f"{signal.inst_id}:{signal.side}",
    )
    context = SignalScanContext(
        dataset="test",
        signal_timeframe="15m",
        trend_timeframe="1h",
        strategy_params=StrategyParams(),
        risk_config=RiskConfig(),
        ledger=Ledger("portfolio", init_capital=10000, equity=10000),
        quality_gate_allows_push=True,
        min_vote_approval_rate=0.4,
        mode="test_manual_confirmation_only",
        min_history_bars=5,
        expected_latest_closed=pd.Timestamp("2026-01-01T02:45:00Z"),
        now=pd.Timestamp("2026-01-01T03:05:00Z"),
        shadow_score_min_closed=9,
    )

    result = asyncio.run(service.scan_cycle(["BTC-USDT-SWAP"], context))

    assert len(result.cycle_health) == 1
    assert result.cycle_health[0]["reason"] == "ready"
    assert result.cycle_health[0]["tier"] == "A"
    assert result.ready_candidates[0].notify_key == "BTC-USDT-SWAP:long"
    assert result.ready_candidates[0].payload["mode"] == "test_manual_confirmation_only"
    assert shadow_ledger.min_closed_values == [9]
    assert lifecycle.recorded


def test_signal_scan_service_respects_checked_bar_gate() -> None:
    frame = _frame()
    checked = {"BTC-USDT-SWAP": str(frame["ts"].iloc[-1])}

    async def loader(_inst_id: str, _limit: int) -> pd.DataFrame:
        return frame

    service = SignalScanService(
        candle_loader=loader,
        regime_manager=FakeRegimeManager(),
        quality_model_shadow=FakeQualityShadow(),
        lifecycle_store=FakeLifecycleStore(),
        shadow_ledger=FakeShadowLedger(),
    )
    context = SignalScanContext(
        dataset="test",
        signal_timeframe="15m",
        trend_timeframe="1h",
        strategy_params=StrategyParams(),
        risk_config=RiskConfig(),
        ledger=Ledger("portfolio", init_capital=10000, equity=10000),
        quality_gate_allows_push=True,
        min_vote_approval_rate=0.4,
        mode="test_manual_confirmation_only",
        min_history_bars=5,
        checked_bars=checked,
        expected_latest_closed=pd.Timestamp("2026-01-01T02:45:00Z"),
        now=pd.Timestamp("2026-01-01T03:05:00Z"),
    )

    result = asyncio.run(service.scan_cycle(["BTC-USDT-SWAP"], context))

    assert result.cycle_health[0]["reason"] == "waiting_next_bar"
    assert result.ready_candidates == []


def test_signal_scan_service_rejects_future_closed_bar() -> None:
    async def loader(_inst_id: str, _limit: int) -> pd.DataFrame:
        return _frame()

    service = SignalScanService(
        candle_loader=loader,
        regime_manager=FakeRegimeManager(),
        quality_model_shadow=FakeQualityShadow(),
        lifecycle_store=FakeLifecycleStore(),
        shadow_ledger=FakeShadowLedger(),
    )
    context = SignalScanContext(
        dataset="test",
        signal_timeframe="15m",
        trend_timeframe="1h",
        strategy_params=StrategyParams(),
        risk_config=RiskConfig(),
        ledger=Ledger("portfolio", init_capital=10000, equity=10000),
        quality_gate_allows_push=True,
        min_vote_approval_rate=0.4,
        mode="test_manual_confirmation_only",
        min_history_bars=5,
        expected_latest_closed=pd.Timestamp("2026-01-01T02:30:00Z"),
        now=pd.Timestamp("2026-01-01T03:05:00Z"),
    )

    result = asyncio.run(service.scan_cycle(["BTC-USDT-SWAP"], context))

    assert result.cycle_health[0]["reason"] == "future_closed_bar"
    assert result.ready_candidates == []


def test_signal_scan_service_retries_feature_error_bar(monkeypatch) -> None:
    checked = {}

    async def loader(_inst_id: str, _limit: int) -> pd.DataFrame:
        return _frame()

    def fail_build_feature_frame(*_args, **_kwargs):
        raise RuntimeError("feature build failed")

    monkeypatch.setattr("okx_signal_system.signal_service.scan.build_feature_frame", fail_build_feature_frame)
    service = SignalScanService(
        candle_loader=loader,
        regime_manager=FakeRegimeManager(),
        quality_model_shadow=FakeQualityShadow(),
        lifecycle_store=FakeLifecycleStore(),
        shadow_ledger=FakeShadowLedger(),
    )
    context = SignalScanContext(
        dataset="test",
        signal_timeframe="15m",
        trend_timeframe="1h",
        strategy_params=StrategyParams(),
        risk_config=RiskConfig(),
        ledger=Ledger("portfolio", init_capital=10000, equity=10000),
        quality_gate_allows_push=True,
        min_vote_approval_rate=0.4,
        mode="test_manual_confirmation_only",
        min_history_bars=5,
        checked_bars=checked,
        expected_latest_closed=pd.Timestamp("2026-01-01T02:45:00Z"),
        now=pd.Timestamp("2026-01-01T03:05:00Z"),
    )

    result = asyncio.run(service.scan_cycle(["BTC-USDT-SWAP"], context))

    assert result.cycle_health[0]["reason"] == "feature_error"
    assert checked == {}


def test_signal_scan_service_retries_invalid_features_bar(monkeypatch) -> None:
    checked = {}

    async def loader(_inst_id: str, _limit: int) -> pd.DataFrame:
        return _frame()

    monkeypatch.setattr("okx_signal_system.signal_service.scan.build_feature_frame", lambda frame, **_kwargs: frame.copy())
    service = SignalScanService(
        candle_loader=loader,
        regime_manager=FakeRegimeManager(),
        quality_model_shadow=FakeQualityShadow(),
        lifecycle_store=FakeLifecycleStore(),
        shadow_ledger=FakeShadowLedger(),
    )
    context = SignalScanContext(
        dataset="test",
        signal_timeframe="15m",
        trend_timeframe="1h",
        strategy_params=StrategyParams(),
        risk_config=RiskConfig(),
        ledger=Ledger("portfolio", init_capital=10000, equity=10000),
        quality_gate_allows_push=True,
        min_vote_approval_rate=0.4,
        mode="test_manual_confirmation_only",
        min_history_bars=5,
        checked_bars=checked,
        expected_latest_closed=pd.Timestamp("2026-01-01T02:45:00Z"),
        now=pd.Timestamp("2026-01-01T03:05:00Z"),
    )

    result = asyncio.run(service.scan_cycle(["BTC-USDT-SWAP"], context))

    assert result.cycle_health[0]["reason"] == "invalid_features"
    assert checked == {}
