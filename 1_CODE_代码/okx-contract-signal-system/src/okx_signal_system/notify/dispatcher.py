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


def _format_beijing_time(value: Any) -> str:
    if value in (None, ""):
        return "-"
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    return ts.tz_convert("Asia/Shanghai").strftime("%Y-%m-%d %H:%M:%S 北京时间")


def _payload_value(payload: dict[str, Any], *names: str) -> Any:
    for name in names:
        value = payload.get(name)
        if value is not None:
            return value
    return None


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

    def send_lifecycle_event(self, event: dict[str, Any]) -> bool:
        if event.get("event_type") == "A_TIER_SIGNAL":
            return self._send_a_tier_outbox_event(event)

        payload = event.get("payload") if isinstance(event.get("payload"), dict) else event
        lifecycle_event = payload.get("lifecycle_event") if isinstance(payload.get("lifecycle_event"), dict) else {}
        status = _payload_value(payload, "status", "state") or event.get("event_type")
        symbol = _payload_value(payload, "symbol", "inst_id") or event.get("signal_id") or "-"
        side = _payload_value(payload, "side") or "-"
        tier = _payload_value(payload, "tier", "level")
        score = _payload_value(payload, "score", "signal_score")
        signal_time = _payload_value(payload, "signal_time", "triggered_at")
        send_time = _now_dispatch_time()
        event_time = lifecycle_event.get("at") or event.get("available_at")
        lines = [
            "OKX signal lifecycle event",
            f"status: {status}",
            f"symbol: {symbol}",
            f"side: {side}",
            f"signal_time: {_format_beijing_time(signal_time)}",
            f"send_time: {send_time}",
        ]
        if tier is not None:
            lines.append(f"tier: {tier}")
        if score is not None:
            try:
                lines.append(f"score: {float(score):.1f}")
            except (TypeError, ValueError):
                lines.append(f"score: {score}")
        if event_time:
            lines.append(f"event_time: {_format_beijing_time(event_time)}")
        if event.get("outbox_id"):
            lines.append(f"outbox_id: {event['outbox_id']}")
        return send_text("\n".join(lines))

    def _send_a_tier_outbox_event(self, event: dict[str, Any]) -> bool:
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        signal = payload.get("signal") if isinstance(payload.get("signal"), dict) else {}
        risk = payload.get("risk") if isinstance(payload.get("risk"), dict) else {}
        lifecycle = payload.get("lifecycle") if isinstance(payload.get("lifecycle"), dict) else {}
        reason_codes = signal.get("reason_codes") or ()
        if isinstance(reason_codes, str):
            reason = reason_codes
        else:
            reason = ", ".join(str(code) for code in reason_codes if code)
        return send_signal_observation(
            inst_id=str(signal.get("inst_id") or payload.get("symbol") or event.get("signal_id") or "-"),
            side=str(signal.get("side") or payload.get("side") or "-"),
            entry_ref=float(signal.get("entry_ref") or 0),
            stop_loss=float(signal.get("stop_loss") or 0),
            take_profit=float(signal.get("take_profit") or 0),
            reason=reason,
            signal_score=float(signal.get("signal_score") or risk.get("signal_score") or 0),
            risk_reward_ratio=risk.get("risk_reward_ratio") or signal.get("risk_reward_ratio"),
            stop_reason=str(risk.get("stop_reason") or ""),
            tp_reason=str(risk.get("tp_reason") or ""),
            kline_time=pd.Timestamp(signal.get("ts")).isoformat() if signal.get("ts") else "",
            signal_timeframe=str(payload.get("signal_timeframe") or ""),
            trend_timeframe=str(payload.get("trend_timeframe") or ""),
            tier=payload.get("tier"),
            rank=payload.get("rank"),
            total_candidates=payload.get("total_candidates") or payload.get("total_formal_candidates"),
            lifecycle_status=lifecycle.get("status") or payload.get("lifecycle_status"),
            invalidation_price=signal.get("invalidation_price") or signal.get("stop_loss"),
            quality_model=payload.get("quality_model") if isinstance(payload.get("quality_model"), dict) else None,
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


def _now_dispatch_time() -> str:
    return pd.Timestamp.now(tz="Asia/Shanghai").strftime("%Y-%m-%d %H:%M:%S 北京时间")


__all__ = ["NotificationDispatcher"]
