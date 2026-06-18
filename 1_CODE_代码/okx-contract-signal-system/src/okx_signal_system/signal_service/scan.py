from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from okx_signal_system.data.closed_backfill import latest_closed_candle_start
from okx_signal_system.data.loader import closed_bars
from okx_signal_system.features.indicators import build_feature_frame
from okx_signal_system.risk.model import RiskConfig, RiskDecision, validate_signal
from okx_signal_system.signal_quality.candidate import CandidateLike, ObservationCandidate, SignalCandidate
from okx_signal_system.signal_quality.correlation import DEFAULT_MIN_CORRELATION_SAMPLES
from okx_signal_system.signal_quality.lifecycle import SignalLifecycleStore, lifecycle_payload
from okx_signal_system.signal_quality.observation import (
    NEAR_BREAKOUT_DISTANCE_ATR,
    breakout_distance_atr,
    near_breakout_observation,
)
from okx_signal_system.signal_quality.quality_shadow import QualityModelShadowScorer
from okx_signal_system.signal_quality.selector import TieredSelection
from okx_signal_system.signal_quality.selector import (
    DEFAULT_MAX_A_PER_CORRELATION_GROUP,
    DEFAULT_MAX_A_PER_CYCLE,
    DEFAULT_MIN_A_QUALITY_SCORE,
    DEFAULT_MIN_B_QUALITY_SCORE,
    assign_tiers,
)
from okx_signal_system.signal_service.runtime import is_latest_bar_fresh
from okx_signal_system.signal_runtime import DEFAULT_MAX_SIGNAL_LAG_MINUTES, signal_is_stale
from okx_signal_system.strategy.ensemble import ensemble_vote
from okx_signal_system.strategy.trend_breakout import StrategyParams, TradeSignal, build_signal
from okx_signal_system.strategy.vote_gate import vote_gate_passed
from okx_signal_system.timeframe import timeframe_spec


def live_signal_history_limit(params: StrategyParams, *, signal_timeframe: str, trend_timeframe: str) -> int:
    signal_spec = timeframe_spec(signal_timeframe)
    trend_spec = timeframe_spec(trend_timeframe)
    trend_ratio = max(1, trend_spec.minutes // signal_spec.minutes)
    return max(
        3500,
        params.slow_ema + params.breakout_window + params.max_hold_bars + 240,
        params.slow_ema * trend_ratio + params.breakout_window + 240,
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


def candidate_rank_score(*, final_score: float, decision: RiskDecision) -> float:
    rr = float(decision.risk_reward_ratio or 0.0)
    stop_pct = float(decision.stop_distance_pct or 0.0)
    return float(final_score) + min(rr, 8.0) * 0.15 - min(stop_pct, 0.05) * 2.0


def _signal_risk_payload(decision: RiskDecision) -> dict[str, Any]:
    return {
        "accepted": bool(decision.accepted),
        "reason": decision.reason,
        "expected_move_pct": decision.expected_move_pct,
        "failure_probability": decision.failure_probability,
        "volatility_adjusted_score": decision.volatility_adjusted_score,
        "stop_distance_pct": decision.stop_distance_pct,
        "risk_reward_ratio": decision.risk_reward_ratio,
        "cost_buffer_pct": decision.cost_buffer_pct,
        "signal_score": decision.signal_score,
        "stop_reason": decision.stop_reason,
        "tp_reason": decision.tp_reason,
    }


@dataclass(frozen=True)
class SignalScanContext:
    dataset: str
    signal_timeframe: str
    trend_timeframe: str
    strategy_params: StrategyParams
    risk_config: RiskConfig
    ledger: Any | None
    quality_gate_allows_push: bool
    min_vote_approval_rate: float
    mode: str
    min_history_bars: int = 50
    max_signal_lag_minutes: float = DEFAULT_MAX_SIGNAL_LAG_MINUTES
    settle_seconds: int = 60
    expected_latest_closed: datetime | pd.Timestamp | None = None
    now: datetime | pd.Timestamp | None = None
    checked_bars: dict[str, str] | None = None
    send_health_report: bool = False
    shadow_score_min_closed: int = 6
    correlation_min_samples: int = DEFAULT_MIN_CORRELATION_SAMPLES
    max_a_per_cycle: int = DEFAULT_MAX_A_PER_CYCLE
    max_a_per_correlation_group: int = DEFAULT_MAX_A_PER_CORRELATION_GROUP
    min_a_quality_score: float = DEFAULT_MIN_A_QUALITY_SCORE
    min_b_quality_score: float = DEFAULT_MIN_B_QUALITY_SCORE


@dataclass(frozen=True)
class SignalScanResult:
    cycle_health: list[dict[str, Any]]
    ready_candidates: list[SignalCandidate]
    observation_candidates: list[ObservationCandidate]
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
        observation_candidates: list[ObservationCandidate] = []
        candidate_history: dict[str, pd.DataFrame] = {}
        completed_checked_bars: dict[str, str] = {}

        for symbol in symbols:
            inst_id = str(symbol)
            history_limit = live_signal_history_limit(
                context.strategy_params,
                signal_timeframe=context.signal_timeframe,
                trend_timeframe=context.trend_timeframe,
            )
            raw_frame = await self._candle_loader(inst_id, history_limit)
            try:
                df = closed_bars(raw_frame)
            except ValueError as exc:
                cycle_health.append(
                    self._candidate_health_item(
                        inst_id=inst_id,
                        reason="invalid_closed_bar_schema",
                        risk_reason=str(exc),
                    )
                )
                continue
            if len(df) < context.min_history_bars:
                cycle_health.append(self._candidate_health_item(inst_id=inst_id, reason="history_too_short"))
                continue

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

            try:
                regime, _adaptive_params = self._regime_mgr.update_regime(features)
                signal = build_signal(
                    latest_row,
                    inst_id=inst_id,
                    params=context.strategy_params,
                    frame=features,
                    idx=len(features) - 1,
                )
                candidate_history[inst_id] = df
                if self._lifecycle_store is not None:
                    self._lifecycle_store.update_symbol(inst_id, df)
                if not signal.accepted:
                    observation = self._observation_candidate(
                        inst_id=inst_id,
                        row=latest_row,
                        signal=signal,
                        context=context,
                        regime=regime,
                    )
                    if observation is not None:
                        observation_candidates.append(observation)
                        cycle_health.append(observation.health_item)
                    else:
                        cycle_health.append(
                            self._candidate_health_item(
                                inst_id=inst_id,
                                reason=signal.reject_reason or "signal_rejected",
                                row=latest_row,
                                signal=signal,
                                regime=regime,
                            )
                        )
                    if checked_bar_ts is not None:
                        completed_checked_bars[inst_id] = checked_bar_ts
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
                decision = validate_signal(signal, context.ledger, context.risk_config)
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

                notify_key = self._notify_key(signal)
                payload = {
                    "signal": asdict(signal),
                    "risk": _signal_risk_payload(decision),
                    "live_order_enabled": False,
                    "mode": context.mode,
                    "dataset": context.dataset,
                    "signal_timeframe": context.signal_timeframe,
                    "trend_timeframe": context.trend_timeframe,
                    "selected_params": asdict(context.strategy_params),
                    "quality_model": quality_model,
                }
                if would_push:
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
                if would_push and self._is_observable(signal):
                    ready_candidates.append(
                        self._candidate(
                            signal=signal,
                            decision=decision,
                            notify_key=notify_key,
                            payload=payload,
                            health_item=health_item,
                            effective_score=effective_score,
                        )
                    )
            except Exception as exc:
                cycle_health.append(
                    self._candidate_health_item(
                        inst_id=inst_id,
                        reason="scan_error",
                        row=latest_row,
                        risk_reason=str(exc),
                    )
                )
                continue
            if checked_bar_ts is not None:
                completed_checked_bars[inst_id] = checked_bar_ts

        selection = assign_tiers(
            ready_candidates,
            observation_candidates=observation_candidates,
            max_a_per_cycle=context.max_a_per_cycle,
            max_a_per_correlation_group=context.max_a_per_correlation_group,
            min_a_quality_score=context.min_a_quality_score,
            min_b_quality_score=context.min_b_quality_score,
            price_history=candidate_history,
            min_correlation_samples=context.correlation_min_samples,
        )
        total_formal_candidates = len(selection.tier_a) + len(selection.tier_b)
        total_observations = len(selection.tier_c)
        for candidate in selection.ranked:
            candidate.health_item["tier"] = candidate.tier
            candidate.health_item["rank_score"] = candidate.rank_score
            candidate.health_item["correlation_group"] = candidate.correlation_group
            candidate.health_item["total_formal_candidates"] = total_formal_candidates
            candidate.health_item["total_observations"] = total_observations
            if isinstance(candidate, ObservationCandidate):
                candidate.health_item["watch_rank"] = candidate.watch_rank
                candidate.health_item.pop("rank", None)
                if isinstance(candidate.payload, dict):
                    candidate.payload["tier"] = candidate.tier
                    candidate.payload["watch_rank"] = candidate.watch_rank
                    candidate.payload["total_observations"] = total_observations
                continue
            candidate.health_item["rank"] = candidate.rank
            candidate.health_item["total_candidates"] = total_formal_candidates
            if isinstance(candidate.payload, dict):
                candidate.payload["tier"] = candidate.tier
                candidate.payload["rank"] = candidate.rank
                candidate.payload["total_candidates"] = total_formal_candidates
                candidate.payload["total_formal_candidates"] = total_formal_candidates
                candidate.payload["total_observations"] = total_observations

        if context.checked_bars is not None:
            context.checked_bars.update(completed_checked_bars)

        return SignalScanResult(
            cycle_health=cycle_health,
            ready_candidates=selection.tier_a + selection.tier_b,
            observation_candidates=selection.tier_c,
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
            "breakout_distance_atr": breakout_distance_atr(row),
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

    @staticmethod
    def _candidate(
        *,
        signal: TradeSignal,
        decision: RiskDecision,
        notify_key: str,
        payload: dict[str, Any],
        health_item: dict[str, Any],
        effective_score: float,
    ) -> SignalCandidate:
        return SignalCandidate(
            signal=signal,
            decision=decision,
            notify_key=notify_key,
            payload=payload,
            health_item=health_item,
            rank_score=candidate_rank_score(
                final_score=effective_score,
                decision=decision,
            ),
            raw_score=effective_score,
        )

    @staticmethod
    def _observation_candidate(
        *,
        inst_id: str,
        row: pd.Series,
        signal: TradeSignal,
        context: SignalScanContext,
        regime: str | None,
    ) -> ObservationCandidate | None:
        if signal.reject_reason != "no_breakout":
            return None
        observation = near_breakout_observation(row)
        if observation is None:
            return None

        side, close, breakout_level, gap_pct, distance_atr = observation
        health_item = SignalScanService._candidate_health_item(
            inst_id=inst_id,
            reason="near_breakout_observation",
            row=row,
            signal=None,
            regime=regime,
            risk_reason=signal.reject_reason,
            would_push=False,
        )
        health_item.update(
            {
                "side": side,
                "observation": True,
                "observation_status": "not_triggered",
                "breakout_level": breakout_level,
                "breakout_gap_pct": gap_pct,
                "breakout_distance_atr": distance_atr,
            }
        )
        candle_time = pd.Timestamp(row.get("ts"))
        score = max(0.0, 1.0 - distance_atr / NEAR_BREAKOUT_DISTANCE_ATR)
        payload = {
            "signal": {
                "ts": candle_time.isoformat(),
                "inst_id": inst_id,
                "side": side,
                "entry_ref": None,
                "stop_loss": None,
                "take_profit": None,
                "max_hold_bars": None,
                "reason_codes": ("NEAR_BREAKOUT_OBSERVATION",),
                "reject_reason": "not_triggered",
                "signal_score": None,
                "risk_reward_ratio": None,
            },
            "risk": {"accepted": False, "reason": "not_triggered"},
            "observation": {
                "type": "near_breakout",
                "status": "not_triggered",
                "close": close,
                "breakout_level": breakout_level,
                "breakout_gap_pct": gap_pct,
                "breakout_distance_atr": distance_atr,
            },
            "live_order_enabled": False,
            "mode": context.mode,
            "dataset": context.dataset,
            "signal_timeframe": context.signal_timeframe,
            "trend_timeframe": context.trend_timeframe,
            "selected_params": asdict(context.strategy_params),
        }
        return ObservationCandidate(
            inst_id=inst_id,
            side=side,
            candle_time=candle_time,
            close=close,
            breakout_level=breakout_level,
            breakout_gap_pct=gap_pct,
            payload=payload,
            health_item=health_item,
            rank_score=score,
            raw_score=score,
            breakout_distance_atr=distance_atr,
        )

    @staticmethod
    def _is_observable(signal: TradeSignal) -> bool:
        return bool(
            signal.accepted
            and signal.entry_ref is not None
            and signal.stop_loss is not None
            and signal.take_profit is not None
            and signal.max_hold_bars is not None
        )

    def _shadow_adjustment(self, inst_id: str, side: str, *, min_closed: int) -> float:
        return 0.0

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
