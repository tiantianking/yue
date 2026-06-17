from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pandas as pd

from okx_signal_system.risk.model import Ledger, RiskDecision
from okx_signal_system.signal_quality import TieredSelection
from okx_signal_system.signal_service import SignalScanResult
from okx_signal_system.strategy.trend_breakout import TradeSignal


def test_scheduler_run_cycle_consumes_lifecycle_outbox(monkeypatch) -> None:
    from okx_signal_system import scheduler

    calls: list[str] = []

    class FakeWorker:
        def __init__(self, _store, _dispatcher):
            pass

        def run_once(self):
            calls.append("run_once")
            return {"sent": 0, "failed": 0}

    class FakeSummaryStore:
        def has(self, _key):
            return False

        def mark(self, _key, _metadata):
            return True

    def fake_run_scan_cycle(_symbols, ledger, _params, **kwargs):
        if kwargs.get("include_selection"):
            return [], ledger, TieredSelection(ranked=[], tier_a=[], tier_b=[], tier_c=[])
        return [], ledger

    monkeypatch.setattr(scheduler, "LifecycleOutboxWorker", FakeWorker)
    monkeypatch.setattr(scheduler, "SignalLifecycleStore", lambda: object())
    monkeypatch.setattr(scheduler, "BTierSummaryNotificationStore", lambda: FakeSummaryStore())
    monkeypatch.setattr(scheduler, "_data_defaults", lambda: ("test", "15m", "1h"))
    monkeypatch.setattr(scheduler, "load_symbols_for_scan", lambda _dataset=None: ["BTC-USDT-SWAP"])
    monkeypatch.setattr(scheduler, "run_scan_cycle", fake_run_scan_cycle)

    instance = scheduler.SignalScheduler()

    assert instance.run_once() == []
    assert calls == ["run_once"]


@dataclass(frozen=True)
class _ScanServiceStub:
    selection: TieredSelection

    async def scan_cycle(self, _symbols, _context):
        return SignalScanResult(
            cycle_health=[],
            ready_candidates=[],
            observation_candidates=[],
            candidate_history={},
            selection=self.selection,
        )


def test_live_monitor_loop_consumes_lifecycle_outbox_after_scan(monkeypatch) -> None:
    from okx_signal_system.exchange import realtime

    calls: list[str] = []
    monitor = realtime.LiveSignalMonitor.__new__(realtime.LiveSignalMonitor)
    monitor._running = True
    monitor.api = type(
        "Api",
        (),
        {
            "_watched_symbols": ["BTC-USDT-SWAP"],
            "dataset": "test",
            "timeframe": type("Timeframe", (), {"key": "15m"})(),
            "trend_timeframe": type("Timeframe", (), {"key": "1h"})(),
            "persist_data": lambda self: None,
        },
    )()
    monitor._scan_service = _ScanServiceStub(TieredSelection(ranked=[], tier_a=[], tier_b=[], tier_c=[]))
    monitor._strategy_params = realtime.StrategyParams()
    monitor._risk_cfg = realtime.RiskConfig()
    monitor._ledger = Ledger("portfolio", init_capital=10000, equity=10000)
    monitor._quality_gate_allows_push = True
    monitor._min_vote_approval_rate = 0.4
    monitor._shadow_score_min_closed = 6
    monitor._sent_startup_health_report = True
    monitor._last_candidate_health_report_ts = 0.0
    monitor._publish_tiered_candidates = lambda _selection: asyncio.sleep(0)
    monitor._run_lifecycle_outbox_once = lambda: calls.append("run_once") or {"sent": 0, "failed": 0}
    monitor._write_latest_scan_status = lambda _items, error=None: None
    monitor._send_candidate_health_report = lambda _items: None

    async def stop_after_scan(_delay):
        monitor._running = False

    monkeypatch.setattr(realtime.asyncio, "sleep", stop_after_scan)

    asyncio.run(monitor._monitor_loop())

    assert calls == ["run_once"]


def test_a_tier_outbox_worker_sends_and_marks_sent_once(monkeypatch, tmp_path) -> None:
    from okx_signal_system.notify import dispatcher
    from okx_signal_system.signal_quality import LifecycleOutboxWorker, SignalLifecycleStore

    notify_key = "BTC-USDT-SWAP:long:2026-01-01T00:00:00Z"
    signal = TradeSignal(
        ts=pd.Timestamp("2026-01-01T00:00:00Z"),
        inst_id="BTC-USDT-SWAP",
        side="long",
        entry_ref=100.0,
        stop_loss=98.0,
        take_profit=107.0,
        max_hold_bars=12,
        reason_codes=("TEST",),
        signal_score=8.0,
        risk_reward_ratio=3.5,
    )
    decision = RiskDecision(
        accepted=True,
        reason=None,
        leverage_cap=3.0,
        qty=1.0,
        risk_amount=100.0,
        leverage_used=3.0,
        signal_score=8.0,
        risk_reward_ratio=3.5,
    )
    store = SignalLifecycleStore(tmp_path / "lifecycle.sqlite3")
    record = store.record_signal(
        signal,
        signal_id=notify_key,
        invalidation_price=signal.stop_loss,
        signal_timeframe="15m",
        trend_timeframe="1h",
    )
    assert record is not None

    sent_signals: list[dict] = []
    def fake_send_signal_observation(**kwargs) -> bool:
        sent_signals.append(kwargs)
        return True

    monkeypatch.setattr(dispatcher, "send_signal_observation", fake_send_signal_observation)
    monkeypatch.setattr(dispatcher, "send_text", lambda _text: True)

    candidate = type(
        "Candidate",
        (),
        {
            "signal": signal,
            "decision": decision,
            "notify_key": notify_key,
            "tier": "A",
            "rank": 1,
            "health_item": {"total_candidates": 1},
            "payload": {"lifecycle": {"status": "TRIGGERED"}, "quality_model": {}},
            "invalidation_price": signal.stop_loss,
        },
    )()

    store.enqueue_notification(
        notify_key,
        signal_id=notify_key,
        event_type="A_TIER_SIGNAL",
        payload={
            **candidate.payload,
            "signal": {
                "inst_id": signal.inst_id,
                "side": signal.side,
                "entry_ref": signal.entry_ref,
                "stop_loss": signal.stop_loss,
                "take_profit": signal.take_profit,
                "reason_codes": signal.reason_codes,
                "signal_score": signal.signal_score,
                "risk_reward_ratio": signal.risk_reward_ratio,
                "ts": signal.ts.isoformat(),
            },
            "risk": {
                "signal_score": decision.signal_score,
                "risk_reward_ratio": decision.risk_reward_ratio,
            },
            "signal_timeframe": "15m",
            "trend_timeframe": "1h",
            "tier": "A",
            "rank": 1,
            "total_candidates": 1,
        },
    )

    pending = [item for item in store.pending_notifications() if item["outbox_id"] == notify_key][0]
    assert pending["status"] == "PENDING"
    assert pending["sent_at"] is None

    worker = LifecycleOutboxWorker(store, dispatcher.NotificationDispatcher(store))
    summary = worker.run_once()
    second_summary = worker.run_once()

    assert len(sent_signals) == 1
    assert sent_signals[0]["inst_id"] == "BTC-USDT-SWAP"
    assert sent_signals[0]["tier"] == "A"
    assert summary == {"sent": 2, "failed": 0}
    assert second_summary == {"sent": 0, "failed": 0}
    assert store.pending_notifications() == []


def test_a_tier_dispatcher_does_not_write_notification_status(monkeypatch, tmp_path) -> None:
    from okx_signal_system.notify import dispatcher
    from okx_signal_system.signal_quality import SignalLifecycleStore

    notify_key = "BTC-USDT-SWAP:long:2026-01-01T00:00:00Z"
    signal = TradeSignal(
        ts=pd.Timestamp("2026-01-01T00:00:00Z"),
        inst_id="BTC-USDT-SWAP",
        side="long",
        entry_ref=100.0,
        stop_loss=98.0,
        take_profit=107.0,
        max_hold_bars=12,
        reason_codes=("TEST",),
        signal_score=8.0,
        risk_reward_ratio=3.5,
    )
    decision = RiskDecision(
        accepted=True,
        reason=None,
        leverage_cap=3.0,
        qty=1.0,
        risk_amount=100.0,
        leverage_used=3.0,
        signal_score=8.0,
        risk_reward_ratio=3.5,
    )
    store = SignalLifecycleStore(tmp_path / "lifecycle.sqlite3")
    record = store.record_signal(
        signal,
        signal_id=notify_key,
        invalidation_price=signal.stop_loss,
        signal_timeframe="15m",
        trend_timeframe="1h",
    )
    assert record is not None
    store.enqueue_notification(
        notify_key,
        signal_id=notify_key,
        event_type="A_TIER_SIGNAL",
        payload={"lifecycle": {"signal_id": notify_key, "status": "TRIGGERED"}},
    )

    monkeypatch.setattr(dispatcher, "send_signal_observation", lambda **_kwargs: False)

    assert not dispatcher.NotificationDispatcher(store).send_signal(
        signal,
        decision,
        notify_key=notify_key,
        signal_timeframe="15m",
        trend_timeframe="1h",
    )

    pending = [
        item for item in store.pending_notifications()
        if item["outbox_id"] == notify_key
    ][0]
    assert pending["status"] == "PENDING"
    assert pending["attempt_count"] == 0


def test_repeated_failed_mark_is_idempotent_for_attempt_count(tmp_path) -> None:
    from okx_signal_system.signal_quality import SignalLifecycleStore

    notify_key = "BTC-USDT-SWAP:long:2026-01-01T00:00:00Z"
    signal = TradeSignal(
        ts=pd.Timestamp("2026-01-01T00:00:00Z"),
        inst_id="BTC-USDT-SWAP",
        side="long",
        entry_ref=100.0,
        stop_loss=98.0,
        take_profit=107.0,
        max_hold_bars=12,
        reason_codes=("TEST",),
        signal_score=8.0,
        risk_reward_ratio=3.5,
    )
    store = SignalLifecycleStore(tmp_path / "lifecycle.sqlite3")
    record = store.record_signal(
        signal,
        signal_id=notify_key,
        invalidation_price=signal.stop_loss,
        signal_timeframe="15m",
        trend_timeframe="1h",
    )
    assert record is not None
    store.enqueue_notification(
        notify_key,
        signal_id=notify_key,
        event_type="A_TIER_SIGNAL",
        payload={"lifecycle": {"signal_id": notify_key, "status": "TRIGGERED"}},
    )

    store.mark_notification_failed(notify_key, "first")
    store.mark_notification_failed(notify_key, "second")

    with store._connect() as conn:
        failed = conn.execute(
            "SELECT status, attempt_count, last_error FROM notification_outbox WHERE signal_id = ?",
            (notify_key,),
        ).fetchone()
    assert failed is not None
    assert failed["status"] == "FAILED"
    assert failed["attempt_count"] == 1
    assert failed["last_error"] == "first"
