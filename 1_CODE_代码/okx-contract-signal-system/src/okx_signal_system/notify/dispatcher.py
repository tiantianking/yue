from __future__ import annotations

from typing import Any

import pandas as pd

from okx_signal_system.notify.feishu import (
    send_b_tier_summary,
    send_candidate_health_report,
    send_signal_observation,
    send_status_report,
    send_text,
)
from okx_signal_system.signal_quality import SignalLifecycleStore, lifecycle_payload


class NotificationDispatcher:
    def __init__(self, lifecycle_store: SignalLifecycleStore | None = None):
        self._lifecycle_store = lifecycle_store

    def send_signal(
        self,
        signal: Any,
        decision: Any,
        *,
        notify_key: str | None = None,
        signal_timeframe: str,
        trend_timeframe: str,
        tier: str | None = None,
        rank: int | None = None,
        total_candidates: int | None = None,
        lifecycle_status: str | None = None,
        invalidation_price: float | None = None,
        quality_model: dict[str, Any] | None = None,
        reason: str = "",
    ) -> bool:
        lifecycle = None
        if self._lifecycle_store is not None and notify_key:
            record = self._lifecycle_store.get(notify_key)
            if record is not None:
                lifecycle = lifecycle_payload(record)
        sent = send_signal_observation(
            inst_id=signal.inst_id,
            side=signal.side,
            entry_ref=signal.entry_ref or 0,
            stop_loss=signal.stop_loss or 0,
            take_profit=signal.take_profit or 0,
            reason=reason or (", ".join(signal.reason_codes) if signal.reason_codes else ""),
            signal_score=decision.signal_score,
            risk_reward_ratio=decision.risk_reward_ratio,
            stop_reason=decision.stop_reason or "",
            tp_reason=decision.tp_reason or "",
            kline_time=pd.Timestamp(signal.ts).isoformat(),
            signal_timeframe=signal_timeframe,
            trend_timeframe=trend_timeframe,
            tier=tier,
            rank=rank,
            total_candidates=total_candidates,
            lifecycle_status=lifecycle_status or (lifecycle or {}).get("status"),
            invalidation_price=invalidation_price,
            quality_model=quality_model,
        )
        if self._lifecycle_store is not None and notify_key:
            if sent:
                self._lifecycle_store.mark_notification_sent(notify_key)
            else:
                self._lifecycle_store.mark_notification_failed(notify_key, "send_signal_observation_returned_false")
        return sent

    def send_a_tier_signal(self, candidate: Any, *, signal_timeframe: str, trend_timeframe: str) -> bool:
        signal = candidate.signal
        decision = candidate.decision
        return self.send_signal(
            signal,
            decision,
            notify_key=candidate.notify_key,
            signal_timeframe=signal_timeframe,
            trend_timeframe=trend_timeframe,
            tier=candidate.tier,
            rank=candidate.rank,
            total_candidates=candidate.health_item.get("total_candidates"),
            lifecycle_status=(candidate.payload.get("lifecycle") or {}).get("status") if isinstance(candidate.payload, dict) else None,
            invalidation_price=candidate.invalidation_price,
            quality_model=candidate.payload.get("quality_model") if isinstance(candidate.payload, dict) else None,
            reason=", ".join(signal.reason_codes) if signal.reason_codes else "",
        )

    def send_b_tier_summary(
        self,
        candidates: list[Any],
        *,
        total_candidates: int,
        signal_timeframe: str,
        trend_timeframe: str,
    ) -> bool:
        return send_b_tier_summary(
            candidates,
            total_candidates=total_candidates,
            signal_timeframe=signal_timeframe,
            trend_timeframe=trend_timeframe,
        )

    def send_status(self, *, cycle_count: int, status: str, last_signal_count: int | None = None) -> bool:
        return send_status_report(cycle_count=cycle_count, status=status, last_signal_count=last_signal_count)

    def send_candidate_health_report(
        self,
        *,
        items: list[dict[str, Any]],
        push_allowed: bool,
        selected_params: dict[str, Any] | None = None,
    ) -> bool:
        return send_candidate_health_report(
            items=items,
            push_allowed=push_allowed,
            selected_params=selected_params,
        )

    def send_startup(self, *, symbol_count: int, environment: str) -> bool:
        return send_text(
            "\n".join(
                [
                    "OKX signal observation platform started",
                    f"time: {pd.Timestamp.now(tz='Asia/Shanghai').strftime('%Y-%m-%d %H:%M:%S 北京时间')}",
                    f"symbols: {symbol_count}",
                    "mode: SIGNAL_ONLY",
                    "purpose: signal observation and manual review",
                    f"data_environment: {environment}",
                ]
            )
        )


__all__ = ["NotificationDispatcher"]
