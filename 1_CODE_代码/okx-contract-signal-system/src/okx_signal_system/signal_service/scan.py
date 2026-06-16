from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from okx_signal_system.data.closed_backfill import latest_closed_candle_start
from okx_signal_system.data.loader import closed_bars
from okx_signal_system.features.indicators import build_feature_frame
from okx_signal_system.risk.model import Ledger, RiskConfig, RiskDecision, apply_halt_policy, validate_signal
from okx_signal_system.signal_quality import (
    QualityModelShadowScorer,
    SignalCandidate,
    SignalLifecycleStore,
    assign_tiers,
    lifecycle_payload,
)
from okx_signal_system.signal_quality.selector import TieredSelection
from okx_signal_system.signal_runtime import DEFAULT_MAX_SIGNAL_LAG_MINUTES, signal_is_stale
from okx_signal_system.strategy.ensemble import ensemble_vote
from okx_signal_system.strategy.trend_breakout import StrategyParams, TradeSignal, build_signal
from okx_signal_system.strategy.vote_gate import vote_gate_passed
from okx_signal_system.timeframe import timeframe_spec
from okx_signal_system.training.startup_quality import is_latest_bar_fresh


def live_signal_history_limit(params: StrategyParams, *, signal_timeframe: str, trend_timeframe: str) -> int:
    signal_spec = timeframe_spec(signal_timeframe)
    trend_spec = timeframe_spec(trend_timeframe)
    trend_ratio = max(1, trend_spec.minutes // signal_spec.minutes)
    return max(
        600,
        params.slow_ema + params.breakout_window + 120,
        params.slow_ema * trend_ratio + 160,
    )


def breakout_gap_pct(row: pd.Series | None) -> float | None:
    if row is None:
        return None
    try:
        close = float(row.get("close", 0.0))
        if close <= 0:
            return None
        bias = str(row.get("trend_bias", row.get("bias_4h", "flat")))
        if bias == "long":
            level = float(row.get("breakout_high"))
            return max(0.0, (level - close) / close)
        if bias == "short":
            level = float(row.get("breakout_low"))
            return max(0.0, (close - level) / close)
    except (TypeError, ValueError):
        return None
    return None


def candidate_rank_score(*, final_score: float, decision: RiskDecision, shadow_adjustment: float = 0.0) -> float:
    rr = float(decision.risk_reward_ratio or 0.0)
    leverage = float(decision.leverage_used or 0.0)
    return float(final_score) + min(rr, 8.0) * 0.15 + float(shadow_adjustment or 0.0) - max(0.0, leverage - 5.0) * 0.05


@dataclass(frozen=True)
class SignalScanContext:
    dataset: str
    signal_timeframe: str
    trend_timeframe: str
    strategy_params: StrategyParams
    risk_config: RiskConfig
    ledger: Ledger
    quality_gate_allows_push: bool
    min_vote_approval_rate: float
    mode: str
    min_history_bars: int = 50
    max_signal_lag_minutes: float = DEFAULT_MAX_SIGNAL_LAG_MINUTES
    settle_seconds: int = 60
    expected_latest_closed: datetime | pd.Timestamp | None = None
    now: datetime | pd.Timestamp | None = None
    position_symbols: frozenset[str] = frozenset()
    checked_bars: dict[str, str] | None = None
    send_health_report: bool = False
    shadow_score_min_closed: int = 6


@dataclass(frozen=True)
class SignalScanResult:
    cycle_health: list[dict[str, Any]]
    ready_candidates: list[SignalCandidate]
    candidate_history: dict[str, pd.DataFrame]
    selection: TieredSelection


class SignalScanService:
    """Shared signal scan decision core for CLI and GUI callers.

    The service does not send notifications, write status files, or place orders.
    Callers keep those side effects at their own boundary.
    """

    def __init__(
        self,
        *,
        candle_loader: Callable[[str, int], Any],
        regime_manager: Any,
        quality_model_shadow: QualityModelShadowScorer | None = None,
        lifecycle_store: SignalLifecycleStore | None = None,
        shadow_ledger: Any | None = None,
        notify_key_builder: Callable[[TradeSignal], str] | None = None,
        quality_model_factory: Callable[[], QualityModelShadowScorer] | None = None,
    ):
        self._candle_loader = candle_loader
        self._regime_mgr = regime_manager
        self._quality_model_shadow = quality_model_shadow
        self._quality_model_factory = quality_model_factory
        self._lifecycle_store = lifecycle_store
        self._shadow_ledger = shadow_ledger
        self._notify_key_builder = notify_key_builder

    async def scan_cycle(self, symbols: Iterable[str], context: SignalScanContext) -> SignalScanResult:
        cycle_health: list[dict[str, Any]] = []
        ready_candidates: list[SignalCandidate] = []
        candidate_history: dict[str, pd.DataFrame] = {}

        for symbol in symbols:
            inst_id = str(symbol)
            if inst_id in context.position_symbols:
                cycle_health.append(self._candidate_health_item(inst_id=inst_id, reason="position_open"))
                continue

            history_limit = live_signal_history_limit(
                context.strategy_params,
                signal_timeframe=context.signal_timeframe,
                trend_timeframe=context.trend_timeframe,
            )
            raw_frame = await self._candle_loader(inst_id, history_limit)
            df = closed_bars(raw_frame)
            if len(df) < context.min_history_bars:
                cycle_health.append(self._candidate_health_item(inst_id=inst_id, reason="history_too_short"))
                continue

            candidate_history[inst_id] = df
            if self._shadow_ledger is not None:
                self._shadow_ledger.update_symbol(inst_id, df)
            if self._lifecycle_store is not None:
                self._lifecycle_store.update_symbol(inst_id, df)

            latest_closed = pd.to_datetime(df["ts"].iloc[-1], utc=True)
            expected_closed = self._expected_latest_closed(context)
            if latest_closed < expected_closed:
                cycle_health.append(self._candidate_health_item(inst_id=inst_id, reason="missing_latest_closed_bar"))
                continue
            if latest_closed > expected_closed:
                cycle_health.append(self._candidate_health_item(inst_id=inst_id, reason="future_closed_bar"))
                continue
            if signal_is_stale(
                latest_closed,
                timeframe=context.signal_timeframe,
                now=context.now,
                max_lag_minutes=context.max_signal_lag_minutes,
            ):
                cycle_health.append(self._candidate_health_item(inst_id=inst_id, reason="stale_signal_bar"))
                continue
            checked_bar_ts: str | None = None
            if context.checked_bars is not None:
                last_ts = str(df["ts"].iloc[-1])
                is_new_bar = context.checked_bars.get(inst_id) != last_ts
                if not is_new_bar and not context.send_health_report:
                    cycle_health.append(self._candidate_health_item(inst_id=inst_id, reason="waiting_next_bar"))
                    continue
                if is_new_bar:
                    checked_bar_ts = last_ts
            if not is_latest_bar_fresh(
                df,
                max_lag_hours=timeframe_spec(context.signal_timeframe).fresh_lag_hours,
                now=pd.Timestamp(context.now) if context.now is not None else None,
            ):
                cycle_health.append(self._candidate_health_item(inst_id=inst_id, reason="stale_data"))
                continue

            try:
                features = build_feature_frame(
                    df,
                    fast_ema=context.strategy_params.fast_ema,
                    slow_ema=context.strategy_params.slow_ema,
                    breakout_window=context.strategy_params.breakout_window,
                    atr_window=context.strategy_params.atr_window,
                    signal_timeframe=context.signal_timeframe,
                    trend_timeframe=context.trend_timeframe,
                )
            except Exception:
                cycle_health.append(self._candidate_health_item(inst_id=inst_id, reason="feature_error"))
                continue

            latest_row = features.iloc[-1]
            if pd.isna(latest_row.get("atr")) or pd.isna(latest_row.get("breakout_high")):
                cycle_health.append(self._candidate_health_item(inst_id=inst_id, reason="invalid_features", row=latest_row))
                continue
            if checked_bar_ts is not None and context.checked_bars is not None:
                context.checked_bars[inst_id] = checked_bar_ts

            regime, _adaptive_params = self._regime_mgr.update_regime(features)
            signal = build_signal(
                latest_row,
                inst_id=inst_id,
                params=context.strategy_params,
                frame=features,
                idx=len(features) - 1,
            )
            if not signal.accepted:
                cycle_health.append(
                    self._candidate_health_item(
                        inst_id=inst_id,
                        reason=signal.reject_reason or "signal_rejected",
                        row=latest_row,
                        signal=signal,
                        regime=regime,
                    )
                )
                continue

            ensemble_result = ensemble_vote(
                latest_row,
                context.strategy_params,
                features,
                len(features) - 1,
                base_score=signal.signal_score or 5.0,
                base_signal=signal,
            )
            effective_score = ensemble_result.final_score
            vote_ok = vote_gate_passed(
                ensemble_result.final_side,
                signal.side,
                ensemble_result.approval_rate,
                context.min_vote_approval_rate,
            )
            if ensemble_result.final_side == "flat":
                effective_score = max(1.0, effective_score - 3.0)
            elif ensemble_result.final_side != signal.side:
                effective_score = max(1.0, effective_score - 1.5)
            penalty = self._regime_mgr.get_score_penalty()
            if penalty < 0:
                effective_score = max(1.0, effective_score + penalty)
            shadow_adjustment = self._shadow_adjustment(
                inst_id,
                signal.side,
                min_closed=context.shadow_score_min_closed,
            )
            if shadow_adjustment:
                effective_score = max(1.0, min(10.0, effective_score + shadow_adjustment))
            quality_model = self._quality_model_scorer().score(signal, features).as_dict()

            signal = replace(signal, signal_score=effective_score)
            risk_cfg = replace(
                context.risk_config,
                max_leverage=max(1.0, min(10.0, context.risk_config.max_leverage * self._regime_mgr.get_leverage_factor())),
            )
            decision = validate_signal(signal, apply_halt_policy(context.ledger, context.risk_config), risk_cfg)
            would_push = bool(
                decision.accepted
                and effective_score >= context.risk_config.min_signal_score
                and context.quality_gate_allows_push
                and vote_ok
            )
            health_reason = self._health_reason(
                ensemble_side=ensemble_result.final_side,
                signal_side=signal.side,
                approval_rate=ensemble_result.approval_rate,
                min_vote_rate=context.min_vote_approval_rate,
                would_push=would_push,
                quality_gate_allows_push=context.quality_gate_allows_push,
                decision=decision,
                effective_score=effective_score,
                min_signal_score=context.risk_config.min_signal_score,
            )
            health_item = self._candidate_health_item(
                inst_id=inst_id,
                reason=health_reason,
                row=latest_row,
                signal=signal,
                regime=regime,
                final_score=effective_score,
                risk_reason=decision.reason,
                shadow_adjustment=shadow_adjustment,
                quality_model=quality_model,
                would_push=would_push,
            )
            cycle_health.append(health_item)

            if would_push:
                notify_key = self._notify_key(signal)
                payload = {
                    "signal": asdict(signal),
                    "risk": asdict(decision),
                    "live_order_enabled": False,
                    "mode": context.mode,
                    "dataset": context.dataset,
                    "signal_timeframe": context.signal_timeframe,
                    "trend_timeframe": context.trend_timeframe,
                    "selected_params": asdict(context.strategy_params),
                    "quality_model": quality_model,
                }
                if self._lifecycle_store is not None:
                    lifecycle_record = self._lifecycle_store.record_signal(
                        signal,
                        signal_id=notify_key,
                        invalidation_price=signal.stop_loss,
                        signal_timeframe=context.signal_timeframe,
                        trend_timeframe=context.trend_timeframe,
                    )
                    if lifecycle_record is not None:
                        lifecycle = lifecycle_payload(lifecycle_record)
                        payload["signal"]["invalidation_price"] = lifecycle_record.invalidation_price
                        payload["lifecycle"] = lifecycle
                        health_item["invalidation_price"] = lifecycle_record.invalidation_price
                        health_item["lifecycle_status"] = lifecycle_record.status
                        health_item["lifecycle"] = lifecycle
                ready_candidates.append(
                    SignalCandidate(
                        signal=signal,
                        decision=decision,
                        notify_key=notify_key,
                        payload=payload,
                        health_item=health_item,
                        rank_score=candidate_rank_score(
                            final_score=effective_score,
                            decision=decision,
                            shadow_adjustment=shadow_adjustment,
                        ),
                        raw_score=effective_score,
                    )
                )

        selection = assign_tiers(ready_candidates, max_tier_a=2, price_history=candidate_history)
        for candidate in selection.ranked:
            candidate.health_item["tier"] = candidate.tier
            candidate.health_item["rank"] = candidate.rank
            candidate.health_item["rank_score"] = candidate.rank_score
            candidate.health_item["correlation_group"] = candidate.correlation_group

        return SignalScanResult(
            cycle_health=cycle_health,
            ready_candidates=selection.ranked,
            candidate_history=candidate_history,
            selection=selection,
        )

    @staticmethod
    def _expected_latest_closed(context: SignalScanContext) -> pd.Timestamp:
        if context.expected_latest_closed is not None:
            expected = pd.Timestamp(context.expected_latest_closed)
            if expected.tzinfo is None:
                return expected.tz_localize("UTC")
            return expected.tz_convert("UTC")
        return pd.Timestamp(
            latest_closed_candle_start(
                context.signal_timeframe,
                now=context.now,
                settle_seconds=context.settle_seconds,
            )
        )

    @staticmethod
    def _candidate_health_item(
        *,
        inst_id: str,
        reason: str,
        row: pd.Series | None = None,
        signal: TradeSignal | None = None,
        regime: str | None = None,
        final_score: float | None = None,
        risk_reason: str | None = None,
        shadow_adjustment: float | None = None,
        quality_model: dict[str, Any] | None = None,
        would_push: bool = False,
    ) -> dict[str, Any]:
        raw_score = signal.signal_score if signal and signal.signal_score is not None else None
        side = signal.side if signal and signal.accepted else None
        kline_time = None
        close = None
        if row is not None:
            try:
                kline_time = pd.Timestamp(row.get("ts")).isoformat()
            except Exception:
                kline_time = str(row.get("ts", ""))
            try:
                close = float(row.get("close"))
            except (TypeError, ValueError):
                close = None
        return {
            "symbol": inst_id,
            "reason": reason,
            "risk_reason": risk_reason,
            "would_push": would_push,
            "side": side,
            "kline_time": kline_time,
            "close": close,
            "bias": str(row.get("trend_bias", row.get("bias_4h", ""))) if row is not None else None,
            "regime": regime,
            "raw_score": float(raw_score) if raw_score is not None else None,
            "final_score": float(final_score) if final_score is not None else None,
            "shadow_adjustment": float(shadow_adjustment) if shadow_adjustment is not None else None,
            "quality_model": quality_model,
            "breakout_gap_pct": breakout_gap_pct(row),
        }

    @staticmethod
    def _health_reason(
        *,
        ensemble_side: str,
        signal_side: str,
        approval_rate: float,
        min_vote_rate: float,
        would_push: bool,
        quality_gate_allows_push: bool,
        decision: RiskDecision,
        effective_score: float,
        min_signal_score: float,
    ) -> str:
        if ensemble_side == "flat":
            return "vote_flat"
        if ensemble_side != signal_side:
            return "vote_side_mismatch"
        if approval_rate < min_vote_rate:
            return "vote_support_too_low"
        if would_push:
            return "ready"
        if not quality_gate_allows_push:
            return "quality_gate_blocked"
        if not decision.accepted:
            return f"risk_{decision.reason or 'rejected'}"
        if effective_score < min_signal_score:
            return "score_below_6"
        return "not_ready"

    def _shadow_adjustment(self, inst_id: str, side: str, *, min_closed: int) -> float:
        if self._shadow_ledger is None:
            return 0.0
        return float(self._shadow_ledger.score_adjustment(inst_id, side, min_closed=min_closed))

    def _quality_model_scorer(self) -> QualityModelShadowScorer:
        if self._quality_model_shadow is None:
            if self._quality_model_factory is not None:
                self._quality_model_shadow = self._quality_model_factory()
            else:
                self._quality_model_shadow = QualityModelShadowScorer()
        return self._quality_model_shadow

    def _notify_key(self, signal: TradeSignal) -> str:
        if self._notify_key_builder is not None:
            return self._notify_key_builder(signal)
        return "|".join([signal.inst_id, signal.side, pd.Timestamp(signal.ts).isoformat()])


__all__ = [
    "SignalScanContext",
    "SignalScanResult",
    "SignalScanService",
    "breakout_gap_pct",
    "candidate_rank_score",
    "live_signal_history_limit",
]
