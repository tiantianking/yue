from __future__ import annotations

import asyncio
from dataclasses import dataclass

from okx_signal_system.risk.model import Ledger
from okx_signal_system.signal_quality import TieredSelection
from okx_signal_system.signal_service import SignalScanResult


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
