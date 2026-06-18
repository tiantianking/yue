import pandas as pd
from types import SimpleNamespace

from okx_signal_system.risk.model import Ledger, RiskDecision
from okx_signal_system.signal_quality import SignalCandidate, TieredSelection
from okx_signal_system.strategy.trend_breakout import StrategyParams, TradeSignal
from okx_signal_system import scheduler
from okx_signal_system.notify import signal_dedupe


class _LifecycleStore:
    def __init__(self):
        self.enqueued = []

    def enqueue_notification(self, key, **metadata) -> None:
        self.enqueued.append((key, metadata))


class _Dispatcher:
    def __init__(self):
        self.b_summaries = []
        self.a_signals = []
        self.statuses = []

    def send_a_tier_signal(self, candidate, *, signal_timeframe: str, trend_timeframe: str) -> bool:
        self.a_signals.append((candidate, signal_timeframe, trend_timeframe))
        return True

    def send_b_tier_summary(self, candidates, *, total_candidates: int, signal_timeframe: str, trend_timeframe: str) -> bool:
        self.b_summaries.append((list(candidates), total_candidates, signal_timeframe, trend_timeframe))
        return True

    def enqueue_b_tier_summary(self, _outbox_id, candidates, *, total_candidates: int, signal_timeframe: str, trend_timeframe: str) -> bool:
        self.b_summaries.append((list(candidates), total_candidates, signal_timeframe, trend_timeframe))
        return True

    def send_status(self, **kwargs) -> bool:
        self.statuses.append(kwargs)
        return True

    def send_lifecycle_event(self, event) -> bool:
        return True


class _OutboxWorker:
    calls = 0

    def __init__(self):
        self.run_calls = 0

    def run_once(self) -> dict:
        self.run_calls += 1
        _OutboxWorker.calls += 1
        return {"sent": 0, "failed": 0, "dead_letter": 0}


def _candidate(symbol: str, score: float, *, tier: str, rank: int) -> SignalCandidate:
    signal = TradeSignal(
        ts=pd.Timestamp("2026-01-01T00:00:00Z"),
        inst_id=symbol,
        side="long",
        entry_ref=100.0,
        stop_loss=98.0,
        take_profit=107.0,
        max_hold_bars=12,
        reason_codes=("TEST",),
        signal_score=score,
        risk_reward_ratio=3.5,
    )
    decision = RiskDecision(
        accepted=True,
        reason=None,
        leverage_cap=3.0,
        qty=1.0,
        risk_amount=100.0,
        leverage_used=3.0,
        signal_score=score,
        risk_reward_ratio=3.5,
    )
    return SignalCandidate(
        signal=signal,
        decision=decision,
        notify_key=f"{symbol}:{score}",
        payload={"signal": {"signal_score": score}},
        health_item={"symbol": symbol, "would_push": True},
        rank_score=score,
        raw_score=score,
        tier=tier,
        rank=rank,
        correlation_group=f"group:{symbol}",
    )


def test_scheduler_sends_b_tier_summary_from_scan_selection(monkeypatch) -> None:
    tier_a = _candidate("BTC-USDT-SWAP", 8.0, tier="A", rank=1)
    tier_b = _candidate("ETH-USDT-SWAP", 7.5, tier="B", rank=2)
    selection = TieredSelection(ranked=[tier_a, tier_b], tier_a=[tier_a], tier_b=[tier_b], tier_c=[])
    ledger = Ledger(inst_id="GLOBAL", init_capital=10000.0, equity=10000.0)
    dispatcher = _Dispatcher()
    lifecycle_store = _LifecycleStore()
    outbox_worker = _OutboxWorker()

    monkeypatch.setattr(scheduler, "load_symbols_for_scan", lambda dataset=None: ["BTC-USDT-SWAP", "ETH-USDT-SWAP"])
    monkeypatch.setattr(
        scheduler,
        "run_scan_cycle",
        lambda *args, **kwargs: (
            [
                {
                    "inst_id": tier_a.inst_id,
                    "signal": tier_a.signal,
                    "decision": tier_a.decision,
                    "candidate": tier_a,
                    "payload": tier_a.payload,
                    "ts": "2026-01-01T00:00:00+00:00",
                }
            ],
            ledger,
            selection,
        ),
    )
    monkeypatch.setattr(scheduler, "NotificationDispatcher", lambda lifecycle_store=None: dispatcher)
    monkeypatch.setattr(scheduler, "SignalLifecycleStore", lambda: lifecycle_store)
    monkeypatch.setattr(scheduler, "LifecycleOutboxWorker", lambda store, dispatcher: outbox_worker)
    monkeypatch.setattr(
        scheduler,
        "load_selected_strategy_params_status",
        lambda: SimpleNamespace(ok=True, params=StrategyParams(), reason="approved_manifest_valid"),
    )

    signal_scheduler = scheduler.SignalScheduler(
        dataset="unit",
        params=StrategyParams(),
        signal_timeframe="15m",
        trend_timeframe="1h",
    )

    results = signal_scheduler.run_cycle()

    assert [item["inst_id"] for item in results] == ["BTC-USDT-SWAP"]
    assert dispatcher.a_signals == []
    assert lifecycle_store.enqueued == [
        (
            tier_a.notify_key,
            {
                "signal_id": None,
                "event_type": "A_TIER_SIGNAL",
                "payload": tier_a.payload,
            },
        )
    ]
    assert outbox_worker.run_calls == 1
    assert dispatcher.b_summaries == [([tier_b], 2, "15m", "1h")]
    assert tier_a.health_item["total_candidates"] == 2


def test_b_tier_summary_key_changes_with_version_params_and_candidates(monkeypatch) -> None:
    candidate = _candidate("ETH-USDT-SWAP", 7.5, tier="B", rank=2)
    changed_candidate = _candidate("SOL-USDT-SWAP", 7.5, tier="B", rank=2)

    monkeypatch.setattr(signal_dedupe, "strategy_version", lambda: "v-test-1")
    base_key = signal_dedupe.b_tier_summary_key(
        candidate.candle_time,
        signal_timeframe="15m",
        trend_timeframe="1h",
        params=StrategyParams(),
        candidates=[candidate],
    )
    params_key = signal_dedupe.b_tier_summary_key(
        candidate.candle_time,
        signal_timeframe="15m",
        trend_timeframe="1h",
        params=StrategyParams(fast_ema=121),
        candidates=[candidate],
    )
    candidates_key = signal_dedupe.b_tier_summary_key(
        candidate.candle_time,
        signal_timeframe="15m",
        trend_timeframe="1h",
        params=StrategyParams(),
        candidates=[changed_candidate],
    )
    monkeypatch.setattr(signal_dedupe, "strategy_version", lambda: "v-test-2")
    version_key = signal_dedupe.b_tier_summary_key(
        candidate.candle_time,
        signal_timeframe="15m",
        trend_timeframe="1h",
        params=StrategyParams(),
        candidates=[candidate],
    )

    assert base_key != params_key
    assert base_key != candidates_key
    assert base_key != version_key


def test_scheduler_passes_approved_manifest_gate_into_scan(monkeypatch) -> None:
    captured: dict = {}
    approved = StrategyParams(fast_ema=33)
    ledger = Ledger(inst_id="GLOBAL", init_capital=10000.0, equity=10000.0)

    monkeypatch.setattr(scheduler, "_data_defaults", lambda: ("unit", "15m", "1h"))
    monkeypatch.setattr(scheduler, "load_symbols_for_scan", lambda _dataset=None: ["BTC-USDT-SWAP"])
    monkeypatch.setattr(
        scheduler,
        "load_selected_strategy_params_status",
        lambda: SimpleNamespace(ok=True, params=approved, reason="approved_manifest_valid"),
    )

    def fake_run_scan_cycle(_symbols, current_ledger, params, **kwargs):
        captured["params"] = params
        captured["gate"] = kwargs.get("quality_gate_allows_push")
        return [], current_ledger, TieredSelection(ranked=[], tier_a=[], tier_b=[], tier_c=[])

    monkeypatch.setattr(scheduler, "run_scan_cycle", fake_run_scan_cycle)
    instance = scheduler.SignalScheduler()
    instance.run_once()

    assert captured == {"params": approved, "gate": True}


def test_scheduler_blocks_explicit_params_that_do_not_match_manifest(monkeypatch) -> None:
    captured: dict = {}
    approved = StrategyParams(fast_ema=33)
    supplied = StrategyParams(fast_ema=34)

    monkeypatch.setattr(scheduler, "_data_defaults", lambda: ("unit", "15m", "1h"))
    monkeypatch.setattr(scheduler, "load_symbols_for_scan", lambda _dataset=None: ["BTC-USDT-SWAP"])
    monkeypatch.setattr(
        scheduler,
        "load_selected_strategy_params_status",
        lambda: SimpleNamespace(ok=True, params=approved, reason="approved_manifest_valid"),
    )

    def fake_run_scan_cycle(_symbols, current_ledger, params, **kwargs):
        captured["params"] = params
        captured["gate"] = kwargs.get("quality_gate_allows_push")
        return [], current_ledger, TieredSelection(ranked=[], tier_a=[], tier_b=[], tier_c=[])

    monkeypatch.setattr(scheduler, "run_scan_cycle", fake_run_scan_cycle)
    instance = scheduler.SignalScheduler(params=supplied)
    instance.run_once()

    assert captured == {"params": supplied, "gate": False}
