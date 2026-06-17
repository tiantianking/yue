from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pandas as pd
import pytest

from okx_signal_system.risk.model import Ledger, RiskConfig
from okx_signal_system.signal_service.scan import SignalScanContext, SignalScanService, candidate_rank_score
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
        self.updated = []

    def update_symbol(self, inst_id, _frame):
        self.updated.append(inst_id)
        return 0

    def record_signal(self, signal, **kwargs):
        self.recorded.append((signal, kwargs))
        return None


class FakeShadowLedger:
    def __init__(self):
        self.min_closed_values = []
        self.updated = []

    def update_symbol(self, inst_id, _frame):
        self.updated.append(inst_id)
        return 0

    def score_adjustment(self, _inst_id, _side, *, min_closed=6):
        self.min_closed_values.append(min_closed)
        return 0.0


class FixedShadowLedger(FakeShadowLedger):
    def score_adjustment(self, _inst_id, _side, *, min_closed=6):
        self.min_closed_values.append(min_closed)
        return 0.8


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
    assert result.cycle_health[0]["rank"] == 1
    assert result.cycle_health[0]["total_formal_candidates"] == 1
    assert result.ready_candidates[0].notify_key == "BTC-USDT-SWAP:long"
    assert result.ready_candidates[0].payload["mode"] == "test_manual_confirmation_only"
    assert result.ready_candidates[0].payload["rank"] == 1
    assert result.ready_candidates[0].payload["total_formal_candidates"] == 1
    assert shadow_ledger.min_closed_values == [9]
    assert lifecycle.recorded


def test_signal_scan_service_does_not_suppress_signal_for_account_position(monkeypatch) -> None:
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

    service = SignalScanService(
        candle_loader=loader,
        regime_manager=FakeRegimeManager(),
        quality_model_shadow=FakeQualityShadow(),
        lifecycle_store=FakeLifecycleStore(),
        shadow_ledger=FakeShadowLedger(),
        notify_key_builder=lambda signal: f"{signal.inst_id}:{signal.side}",
    )
    context = SignalScanContext(
        dataset="test",
        signal_timeframe="15m",
        trend_timeframe="1h",
        strategy_params=StrategyParams(),
        risk_config=RiskConfig(),
        ledger=Ledger("portfolio", init_capital=10000, equity=10000, open_positions=1),
        quality_gate_allows_push=True,
        min_vote_approval_rate=0.4,
        mode="test_manual_confirmation_only",
        min_history_bars=5,
        expected_latest_closed=pd.Timestamp("2026-01-01T02:45:00Z"),
        now=pd.Timestamp("2026-01-01T03:05:00Z"),
    )

    result = asyncio.run(service.scan_cycle(["BTC-USDT-SWAP"], context))

    assert result.cycle_health[0]["reason"] == "ready"
    assert len(result.ready_candidates) == 1


def test_signal_scan_service_applies_shadow_adjustment_once(monkeypatch) -> None:
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

    service = SignalScanService(
        candle_loader=loader,
        regime_manager=FakeRegimeManager(),
        quality_model_shadow=FakeQualityShadow(),
        lifecycle_store=FakeLifecycleStore(),
        shadow_ledger=FixedShadowLedger(),
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
    candidate = result.ready_candidates[0]

    assert candidate.health_item["shadow_adjustment"] == 0.8
    assert candidate.raw_score == candidate.signal.signal_score
    assert candidate.rank_score == candidate_rank_score(final_score=candidate.raw_score, decision=candidate.decision)


def test_signal_scan_service_excludes_non_push_formal_candidate_from_ready_and_c(monkeypatch) -> None:
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
    service = SignalScanService(
        candle_loader=loader,
        regime_manager=FakeRegimeManager(),
        quality_model_shadow=FakeQualityShadow(),
        lifecycle_store=lifecycle,
        shadow_ledger=FakeShadowLedger(),
        notify_key_builder=lambda signal: f"{signal.inst_id}:{signal.side}",
    )
    context = SignalScanContext(
        dataset="test",
        signal_timeframe="15m",
        trend_timeframe="1h",
        strategy_params=StrategyParams(),
        risk_config=RiskConfig(min_signal_score=9.5),
        ledger=Ledger("portfolio", init_capital=10000, equity=10000),
        quality_gate_allows_push=True,
        min_vote_approval_rate=0.4,
        mode="test_manual_confirmation_only",
        min_history_bars=5,
        expected_latest_closed=pd.Timestamp("2026-01-01T02:45:00Z"),
        now=pd.Timestamp("2026-01-01T03:05:00Z"),
    )

    result = asyncio.run(service.scan_cycle(["BTC-USDT-SWAP"], context))

    assert result.cycle_health[0]["reason"] == "risk_signal_score_below_threshold"
    assert result.cycle_health[0]["would_push"] is False
    assert result.ready_candidates == []
    assert result.observation_candidates == []
    assert result.selection.tier_c == []
    assert lifecycle.recorded == []


def test_signal_scan_service_places_near_breakout_watch_item_in_tier_c(monkeypatch) -> None:
    async def loader(_inst_id: str, _limit: int) -> pd.DataFrame:
        return _frame()

    def fake_build_feature_frame(frame, **_kwargs):
        out = frame.copy()
        out["atr"] = 2.0
        out["atr_pct"] = 0.02
        out["breakout_high"] = 100.4
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
            side="flat",
            entry_ref=None,
            stop_loss=None,
            take_profit=None,
            max_hold_bars=None,
            reason_codes=("NO_BREAKOUT",),
            reject_reason="no_breakout",
        )

    monkeypatch.setattr("okx_signal_system.signal_service.scan.build_feature_frame", fake_build_feature_frame)
    monkeypatch.setattr("okx_signal_system.signal_service.scan.build_signal", fake_build_signal)

    lifecycle = FakeLifecycleStore()
    service = SignalScanService(
        candle_loader=loader,
        regime_manager=FakeRegimeManager(),
        quality_model_shadow=FakeQualityShadow(),
        lifecycle_store=lifecycle,
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
        expected_latest_closed=pd.Timestamp("2026-01-01T02:45:00Z"),
        now=pd.Timestamp("2026-01-01T03:05:00Z"),
    )

    result = asyncio.run(service.scan_cycle(["BTC-USDT-SWAP"], context))

    assert result.ready_candidates == []
    assert len(result.observation_candidates) == 1
    observation = result.observation_candidates[0]
    assert observation.tier == "C"
    assert observation.inst_id == "BTC-USDT-SWAP"
    assert observation.payload["observation"]["status"] == "not_triggered"
    assert observation.payload["watch_rank"] == 1
    assert observation.payload["total_observations"] == 1
    assert observation.breakout_distance_atr == pytest.approx(0.2)
    assert observation.health_item["breakout_distance_atr"] == pytest.approx(0.2)
    assert observation.health_item["watch_rank"] == 1
    assert observation.health_item["total_observations"] == 1
    assert result.cycle_health[0]["breakout_distance_atr"] == pytest.approx(0.2)
    assert result.selection.tier_c == [observation]
    assert result.cycle_health[0]["reason"] == "near_breakout_observation"
    assert result.cycle_health[0]["observation"] is True
    assert lifecycle.recorded == []


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
    lifecycle = FakeLifecycleStore()
    shadow_ledger = FakeShadowLedger()

    async def loader(_inst_id: str, _limit: int) -> pd.DataFrame:
        return _frame()

    service = SignalScanService(
        candle_loader=loader,
        regime_manager=FakeRegimeManager(),
        quality_model_shadow=FakeQualityShadow(),
        lifecycle_store=lifecycle,
        shadow_ledger=shadow_ledger,
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
    assert lifecycle.recorded == []
    assert lifecycle.updated == []
    assert shadow_ledger.updated == []
    assert shadow_ledger.min_closed_values == []


def test_signal_scan_service_does_not_update_state_before_closed_bar_gate() -> None:
    lifecycle = FakeLifecycleStore()
    shadow_ledger = FakeShadowLedger()

    async def loader(_inst_id: str, _limit: int) -> pd.DataFrame:
        return _frame()

    service = SignalScanService(
        candle_loader=loader,
        regime_manager=FakeRegimeManager(),
        quality_model_shadow=FakeQualityShadow(),
        lifecycle_store=lifecycle,
        shadow_ledger=shadow_ledger,
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
        expected_latest_closed=pd.Timestamp("2026-01-01T03:00:00Z"),
        now=pd.Timestamp("2026-01-01T03:05:00Z"),
    )

    result = asyncio.run(service.scan_cycle(["BTC-USDT-SWAP"], context))

    assert result.cycle_health[0]["reason"] == "missing_latest_closed_bar"
    assert result.candidate_history == {}
    assert lifecycle.recorded == []
    assert lifecycle.updated == []
    assert shadow_ledger.updated == []
    assert shadow_ledger.min_closed_values == []


def test_signal_scan_service_retries_feature_error_bar(monkeypatch) -> None:
    checked = {}
    lifecycle = FakeLifecycleStore()
    shadow_ledger = FakeShadowLedger()

    async def loader(_inst_id: str, _limit: int) -> pd.DataFrame:
        return _frame()

    def fail_build_feature_frame(*_args, **_kwargs):
        raise RuntimeError("feature build failed")

    monkeypatch.setattr("okx_signal_system.signal_service.scan.build_feature_frame", fail_build_feature_frame)
    service = SignalScanService(
        candle_loader=loader,
        regime_manager=FakeRegimeManager(),
        quality_model_shadow=FakeQualityShadow(),
        lifecycle_store=lifecycle,
        shadow_ledger=shadow_ledger,
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
    assert lifecycle.recorded == []
    assert lifecycle.updated == []
    assert shadow_ledger.updated == []


def test_signal_scan_service_retries_invalid_features_bar(monkeypatch) -> None:
    checked = {}
    lifecycle = FakeLifecycleStore()
    shadow_ledger = FakeShadowLedger()

    async def loader(_inst_id: str, _limit: int) -> pd.DataFrame:
        return _frame()

    monkeypatch.setattr("okx_signal_system.signal_service.scan.build_feature_frame", lambda frame, **_kwargs: frame.copy())
    service = SignalScanService(
        candle_loader=loader,
        regime_manager=FakeRegimeManager(),
        quality_model_shadow=FakeQualityShadow(),
        lifecycle_store=lifecycle,
        shadow_ledger=shadow_ledger,
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
    assert lifecycle.recorded == []
    assert lifecycle.updated == []
    assert shadow_ledger.updated == []


def test_signal_scan_service_retries_after_strategy_scan_error(monkeypatch) -> None:
    checked = {}

    async def loader(_inst_id: str, _limit: int) -> pd.DataFrame:
        return _frame()

    def fake_build_feature_frame(frame, **_kwargs):
        out = frame.copy()
        out["atr"] = 2.0
        out["breakout_high"] = 99.0
        out["breakout_low"] = 95.0
        out["trend_bias"] = "long"
        return out

    def fail_build_signal(*_args, **_kwargs):
        raise RuntimeError("strategy failed")

    monkeypatch.setattr("okx_signal_system.signal_service.scan.build_feature_frame", fake_build_feature_frame)
    monkeypatch.setattr("okx_signal_system.signal_service.scan.build_signal", fail_build_signal)
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
        mode="test_signal_only",
        min_history_bars=5,
        checked_bars=checked,
        expected_latest_closed=pd.Timestamp("2026-01-01T02:45:00Z"),
        now=pd.Timestamp("2026-01-01T03:05:00Z"),
    )

    result = asyncio.run(service.scan_cycle(["BTC-USDT-SWAP"], context))

    assert result.cycle_health[0]["reason"] == "scan_error"
    assert result.cycle_health[0]["risk_reason"] == "strategy failed"
    assert result.ready_candidates == []
    assert checked == {}
