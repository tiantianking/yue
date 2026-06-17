"""Periodic OKX signal-only scheduler."""
from __future__ import annotations

import logging
import asyncio
from datetime import datetime, timedelta, timezone
from threading import Event

import pandas as pd

from okx_signal_system.config import load_config, load_runtime_config
from okx_signal_system.data.loader import load_symbol_file
from okx_signal_system.ml.regime_adaptive import AdaptiveParamsManager
from okx_signal_system.paths import find_lightweight_history
from okx_signal_system.notify import NotificationDispatcher
from okx_signal_system.notify.signal_dedupe import BTierSummaryNotificationStore, b_tier_summary_key
from okx_signal_system.risk.model import Ledger
from okx_signal_system.signal_quality import LifecycleOutboxWorker, SignalLifecycleStore, TieredSelection
from okx_signal_system.signal_service import SignalScanContext, SignalScanService
from okx_signal_system.signal_runtime import (
    DEFAULT_MAX_SIGNAL_LAG_MINUTES,
    parameter_hash,
    strategy_version,
)
from okx_signal_system.strategy.trend_breakout import StrategyParams
from okx_signal_system.timeframe import timeframe_spec

log = logging.getLogger(__name__)

BEIJING_TZ = timezone(timedelta(hours=8), "Asia/Shanghai")

SCAN_INTERVAL_SECONDS = 15 * 60
STATUS_INTERVAL_SECONDS = 30 * 60
GLOBAL_INITIAL_EQUITY = 10000.0
DEFAULT_DATASET = "okx_15m_extended"
DEFAULT_SIGNAL_TIMEFRAME = "15m"
DEFAULT_TREND_TIMEFRAME = "1h"


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _now_beijing() -> datetime:
    return datetime.now(BEIJING_TZ)


def _data_defaults() -> tuple[str, str, str]:
    try:
        cfg = load_config("base.yaml")
        data_cfg = cfg.get("data", {})
        dataset = str(data_cfg.get("historical_dataset", DEFAULT_DATASET))
        signal_timeframe = timeframe_spec(data_cfg.get("timeframe", DEFAULT_SIGNAL_TIMEFRAME)).key
        trend_timeframe = timeframe_spec(data_cfg.get("trend_timeframe", DEFAULT_TREND_TIMEFRAME)).key
        return dataset, signal_timeframe, trend_timeframe
    except Exception:
        log.warning("failed to load data defaults; using 15m defaults")
        return DEFAULT_DATASET, DEFAULT_SIGNAL_TIMEFRAME, DEFAULT_TREND_TIMEFRAME


def load_symbols_for_scan(dataset: str = DEFAULT_DATASET) -> list[str]:
    """Load configured scan symbols."""
    try:
        cfg = load_config("base.yaml")
        return cfg.get("data", {}).get("symbols", ["BTC-USDT-SWAP"])
    except Exception:
        log.warning("failed to load scan symbols; using BTC-USDT-SWAP")
        return ["BTC-USDT-SWAP"]


def symbol_to_inst_id(symbol: str) -> str:
    """Convert OKX instrument id to local symbol id."""
    symbol_clean = symbol.replace("-", "_").replace("_SWAP", "")
    # Local files use BTC_USDT_USDT_<timeframe>.parquet naming.
    if symbol_clean.count("USDT") == 1:
        symbol_clean = symbol_clean + "_USDT"
    return symbol_clean


def symbol_to_parquet_filename(symbol: str, timeframe: str = DEFAULT_SIGNAL_TIMEFRAME) -> str:
    """Return the parquet filename for a configured symbol and timeframe."""
    return f"{symbol_to_inst_id(symbol)}_{timeframe_spec(timeframe).file_suffix}.parquet"


def inst_id_to_parquet_filename(inst_id: str, timeframe: str = DEFAULT_SIGNAL_TIMEFRAME) -> str:
    """Return the parquet filename for an internal instrument id."""
    return f"{inst_id}_{timeframe_spec(timeframe).file_suffix}.parquet"


def scan_single_symbol(
    inst_id: str,
    ledger: Ledger,
    params: StrategyParams,
    *,
    dataset: str = DEFAULT_DATASET,
    signal_timeframe: str = DEFAULT_SIGNAL_TIMEFRAME,
    trend_timeframe: str = DEFAULT_TREND_TIMEFRAME,
) -> dict | None:
    """Scan a single symbol."""
    results, _ = run_scan_cycle(
        [inst_id],
        ledger,
        params,
        dataset=dataset,
        signal_timeframe=signal_timeframe,
        trend_timeframe=trend_timeframe,
    )
    return results[0] if results else None


def _empty_selection() -> TieredSelection:
    return TieredSelection(ranked=[], tier_a=[], tier_b=[], tier_c=[])


def run_scan_cycle(
    symbols: list[str],
    ledger: Ledger,
    params: StrategyParams,
    *,
    dataset: str = DEFAULT_DATASET,
    signal_timeframe: str = DEFAULT_SIGNAL_TIMEFRAME,
    trend_timeframe: str = DEFAULT_TREND_TIMEFRAME,
    lifecycle_store: SignalLifecycleStore | None = None,
    include_selection: bool = False,
) -> tuple[list[dict], Ledger] | tuple[list[dict], Ledger, TieredSelection]:
    """Execute one scan cycle."""
    signal_timeframe = timeframe_spec(signal_timeframe).key
    trend_timeframe = timeframe_spec(trend_timeframe).key

    async def candle_loader(inst_id: str, limit: int) -> pd.DataFrame:
        root = find_lightweight_history(dataset)
        path = root / inst_id_to_parquet_filename(inst_id, signal_timeframe)
        if not path.exists():
            log.warning("data file does not exist: %s", path)
            return pd.DataFrame()
        return load_symbol_file(path).frame.tail(limit).reset_index(drop=True)

    service = SignalScanService(
        candle_loader=candle_loader,
        regime_manager=AdaptiveParamsManager(),
        lifecycle_store=lifecycle_store or SignalLifecycleStore(),
    )
    context = SignalScanContext(
        dataset=dataset,
        signal_timeframe=signal_timeframe,
        trend_timeframe=trend_timeframe,
        strategy_params=params,
        risk_config=load_runtime_config().risk_config(initial_equity=GLOBAL_INITIAL_EQUITY),
        ledger=ledger,
        quality_gate_allows_push=True,
        min_vote_approval_rate=0.4,
        mode="scheduler_signal_only",
        min_history_bars=100,
        max_signal_lag_minutes=DEFAULT_MAX_SIGNAL_LAG_MINUTES,
    )
    try:
        scan_result = asyncio.run(service.scan_cycle([symbol_to_inst_id(symbol) for symbol in symbols], context))
    except Exception as e:
        log.error("scan cycle failed: %s", e)
        if include_selection:
            return [], ledger, _empty_selection()
        return [], ledger

    ready_results = [
        {
            "inst_id": candidate.inst_id,
            "signal": candidate.signal,
            "decision": candidate.decision,
            "candidate": candidate,
            "payload": candidate.payload,
            "ts": _now_utc().isoformat(),
        }
        for candidate in scan_result.selection.tier_a
    ]
    if not ready_results:
        log.info("no valid scheduler signals this cycle")
    else:
        log.info("scheduler produced %s valid signals", len(ready_results))

    if scan_result.selection.tier_b:
        log.info("scheduler retained %s B-tier candidates", len(scan_result.selection.tier_b))
    if scan_result.selection.tier_c:
        log.info("scheduler observed %s C-tier candidates", len(scan_result.selection.tier_c))
    if include_selection:
        return ready_results, ledger, scan_result.selection
    return ready_results, ledger


def format_signal_summary(signals: list[dict]) -> str:
    """Format signal summary text."""
    if not signals:
        return "no signals"
    lines = [f"scan_time: {_now_beijing().strftime('%Y-%m-%d %H:%M:%S')} Asia/Shanghai"]
    lines.append(f"valid_signals: {len(signals)}")
    for s in signals:
        d = s["decision"]
        sig = s["signal"]
        rr = d.risk_reward_ratio if d.risk_reward_ratio is not None else sig.risk_reward_ratio
        score = d.signal_score if d.signal_score is not None else sig.signal_score
        lines.append(
            f"{s['inst_id']}: "
            f"side={sig.side} | "
            f"score={float(score or 0):.1f} | "
            f"rr={float(rr or 0):.2f}R | "
            f"target={float(sig.take_profit or 0):.4f}"
        )
    return "\n".join(lines)


def format_status_message(ledger: Ledger, cycle_count: int) -> str:
    """Format scheduler status text."""
    return (
        f"system_status\n"
        f"time: {_now_beijing().strftime('%Y-%m-%d %H:%M:%S')} Asia/Shanghai\n"
        f"cycle: #{cycle_count}\n"
        f"mode: SIGNAL_ONLY\n"
        f"status: {ledger.status}\n"
        f"scope: signal research and manual review notifications only"
    )


class SignalScheduler:
    """Periodic signal scheduler."""

    def __init__(
        self,
        dataset: str | None = None,
        params: StrategyParams | None = None,
        signal_timeframe: str | None = None,
        trend_timeframe: str | None = None,
        status_callback=None,
    ):
        default_dataset, default_signal_timeframe, default_trend_timeframe = _data_defaults()
        self.dataset = dataset or default_dataset
        self.params = params or StrategyParams()
        self.signal_timeframe = timeframe_spec(signal_timeframe or default_signal_timeframe).key
        self.trend_timeframe = timeframe_spec(trend_timeframe or default_trend_timeframe).key
        self.status_callback = status_callback
        self._stop_event = Event()
        self._cycle = 0
        self._ledger = Ledger(
            inst_id="GLOBAL",
            init_capital=GLOBAL_INITIAL_EQUITY,
            equity=GLOBAL_INITIAL_EQUITY,
        )
        self._lifecycle_store = SignalLifecycleStore()
        self._notification_dispatcher = NotificationDispatcher(self._lifecycle_store)
        self._lifecycle_outbox_worker = LifecycleOutboxWorker(self._lifecycle_store, self._notification_dispatcher)
        self._b_tier_summary_store = BTierSummaryNotificationStore()
        self._symbols = load_symbols_for_scan(dataset)

    def stop(self):
        self._stop_event.set()

    def is_running(self) -> bool:
        return not self._stop_event.is_set()

    def _b_tier_summary_key(self, candidates) -> str | None:
        if not candidates:
            return None
        return b_tier_summary_key(
            candidates[0].candle_time,
            signal_timeframe=self.signal_timeframe,
            trend_timeframe=self.trend_timeframe,
            params=self.params,
            candidates=candidates,
        )

    def _mark_b_tier_summary_notified(self, key: str, candidates) -> None:
        candle_time = candidates[0].candle_time if candidates else None
        self._b_tier_summary_store.mark(
            key,
            {
                "kline_time": pd.Timestamp(candle_time).isoformat() if candle_time is not None else "",
                "candidate_count": len(candidates),
                "signal_timeframe": self.signal_timeframe,
                "trend_timeframe": self.trend_timeframe,
                "strategy_version": strategy_version(),
                "parameter_hash": parameter_hash(self.params),
            },
        )

    def run_cycle(self) -> list[dict]:
        self._cycle += 1
        log.info("=== scan cycle #%s ===", self._cycle)
        results, self._ledger, selection = run_scan_cycle(
            self._symbols,
            self._ledger,
            self.params,
            dataset=self.dataset,
            signal_timeframe=self.signal_timeframe,
            trend_timeframe=self.trend_timeframe,
            lifecycle_store=self._lifecycle_store,
            include_selection=True,
        )

        total_formal_candidates = len(selection.tier_a) + len(selection.tier_b)
        if results:
            for r in results:
                candidate = r.get("candidate")
                if candidate is None or candidate.tier != "A":
                    continue
                candidate.health_item["total_candidates"] = total_formal_candidates
                self._notification_dispatcher.send_a_tier_signal(
                    candidate,
                    signal_timeframe=self.signal_timeframe,
                    trend_timeframe=self.trend_timeframe,
                )

        if selection.tier_b:
            summary_key = self._b_tier_summary_key(selection.tier_b)
            if summary_key and self._b_tier_summary_store.has(summary_key):
                log.info("scheduler B-tier summary already sent for this candle: %s", summary_key)
            else:
                try:
                    summary_sent = self._notification_dispatcher.send_b_tier_summary(
                        selection.tier_b,
                        total_candidates=total_formal_candidates,
                        signal_timeframe=self.signal_timeframe,
                        trend_timeframe=self.trend_timeframe,
                    )
                except Exception as exc:
                    log.error("scheduler B-tier summary push failed: %s", exc)
                    summary_sent = False
                if summary_sent and summary_key:
                    self._mark_b_tier_summary_notified(summary_key, selection.tier_b)
                    log.info("scheduler B-tier summary sent: %s candidates", len(selection.tier_b))
                elif summary_key:
                    log.warning("scheduler B-tier summary was not delivered; will retry next scan")

        outbox_summary = self._lifecycle_outbox_worker.run_once()
        if outbox_summary.get("sent") or outbox_summary.get("failed") or outbox_summary.get("dead_letter"):
            log.info("lifecycle outbox processed: %s", outbox_summary)

        if self._cycle % 2 == 0:
            self._notification_dispatcher.send_status(
                status=self._ledger.status,
                cycle_count=self._cycle,
                last_signal_count=sum(1 for r in results if r.get("candidate") and r["candidate"].tier == "A"),
            )

        if self.status_callback:
            msg = format_status_message(self._ledger, self._cycle)
            try:
                self.status_callback(msg)
            except Exception as e:
                log.error("status callback failed: %s", e)
        return results

    def run_forever(self):
        """Run scheduler until stopped."""
        log.info(
            "scheduler started: dataset=%s signal_tf=%s trend_tf=%s symbols=%s interval=%sm",
            self.dataset,
            self.signal_timeframe,
            self.trend_timeframe,
            len(self._symbols),
            SCAN_INTERVAL_SECONDS // 60,
        )
        while not self._stop_event.is_set():
            try:
                self.run_cycle()
            except Exception as e:
                log.error("scan cycle exception: %s", e)
            if self._stop_event.is_set():
                break
            next_run = _now_utc().timestamp() + SCAN_INTERVAL_SECONDS
            log.info("next scan: %s UTC", datetime.fromtimestamp(next_run, tz=timezone.utc).strftime("%H:%M:%S"))
            self._stop_event.wait(timeout=SCAN_INTERVAL_SECONDS)

    def run_once(self) -> list[dict]:
        """Run one scheduler cycle."""
        return self.run_cycle()


def run_live_scan():
    """Start live scheduler CLI."""
    import argparse

    parser = argparse.ArgumentParser(description="OKX signal-only scheduler")
    parser.add_argument("--dataset", default=None, help="dataset name")
    parser.add_argument("--signal-timeframe", default=None)
    parser.add_argument("--trend-timeframe", default=None)
    parser.add_argument("--once", action="store_true", help="run once")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    scheduler = SignalScheduler(
        dataset=args.dataset,
        signal_timeframe=args.signal_timeframe,
        trend_timeframe=args.trend_timeframe,
    )
    if args.once:
        results = scheduler.run_once()
        if results:
            print(format_signal_summary(results))
        else:
            print("no valid signals")
    else:
        scheduler.run_forever()
