from __future__ import annotations

from dataclasses import asdict, dataclass
from math import floor
from typing import Any

MIN_REFERENCE_MARGIN_FRACTION = 0.001
MAX_REFERENCE_MARGIN_FRACTION = 0.20
MIN_RISK_BUDGET_FRACTION = 0.001
MAX_RISK_BUDGET_FRACTION = 0.01


@dataclass(frozen=True)
class LeverageAdvice:
    """Signal-only leverage guidance; never an execution or sizing command."""

    recommended_multiple: int
    maximum_multiple: int
    suggested_margin_fraction: float
    estimated_reference_loss_fraction: float
    effective_stop_fraction: float
    binding_constraint: str
    confidence: str
    advisory_only: bool = True

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _finite_float(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    if result != result or result in (float("inf"), float("-inf")):
        return default
    return result


def build_leverage_advice(
    *,
    entry_ref: float,
    stop_loss: float,
    tier: str | None,
    signal_score: float | None,
    risk_reward_ratio: float | None,
    quality_model: dict[str, Any] | None = None,
    risk_budget_fraction: float = 0.005,
    suggested_margin_fraction: float = 0.08,
    fee_slippage_buffer: float = 0.002,
    global_cap: int = 5,
) -> LeverageAdvice | None:
    """Build conservative, normalized leverage guidance for manual review.

    The calculation is independent of credentials, balances, live positions,
    order quantity, margin mode, and private exchange APIs.  It cannot execute
    or size a trade.
    """

    entry = _finite_float(entry_ref)
    stop = _finite_float(stop_loss)
    if entry <= 0 or stop <= 0 or entry == stop:
        return None

    normalized_tier = str(tier or "").upper().strip()
    if normalized_tier not in {"A", "A-", "A_MINUS"}:
        return None

    stop_fraction = abs(entry - stop) / entry
    effective_stop = stop_fraction + max(0.0, _finite_float(fee_slippage_buffer))
    if effective_stop <= 0:
        return None

    margin_fraction = min(
        max(_finite_float(suggested_margin_fraction, 0.08), MIN_REFERENCE_MARGIN_FRACTION),
        MAX_REFERENCE_MARGIN_FRACTION,
    )
    risk_budget = min(
        max(_finite_float(risk_budget_fraction, 0.005), MIN_RISK_BUDGET_FRACTION),
        MAX_RISK_BUDGET_FRACTION,
    )
    hard_cap = min(5, max(1, int(_finite_float(global_cap, 5.0))))

    risk_cap = risk_budget / (margin_fraction * effective_stop)
    cap = min(float(hard_cap), risk_cap)
    constraint = "STOP_RISK_LIMIT" if risk_cap <= hard_cap else "GLOBAL_SIGNAL_CAP"

    def apply_limit(limit: float, reason: str) -> None:
        nonlocal cap, constraint
        bounded_limit = max(0.0, _finite_float(limit))
        if bounded_limit + 1e-12 < cap:
            cap = bounded_limit
            constraint = reason

    if normalized_tier in {"A-", "A_MINUS"}:
        apply_limit(1.0, "SHADOW_SIGNAL_NO_LEVERAGE")

    score = _finite_float(signal_score)
    if score < 7.0:
        apply_limit(1.0, "SIGNAL_CONFIDENCE_LIMIT")
    elif score < 8.0:
        apply_limit(2.0, "SIGNAL_CONFIDENCE_LIMIT")
    elif score < 9.0:
        apply_limit(3.0, "SIGNAL_CONFIDENCE_LIMIT")

    rr = _finite_float(risk_reward_ratio)
    if rr < 2.0:
        apply_limit(1.0, "REWARD_RISK_LIMIT")
    elif rr < 3.0:
        apply_limit(2.0, "REWARD_RISK_LIMIT")

    quality = quality_model if isinstance(quality_model, dict) else {}
    uncertainty = _finite_float(quality.get("uncertainty"), -1.0)
    expected_net_r = _finite_float(quality.get("expected_net_r"), float("-inf"))
    p_sl = _finite_float(quality.get("p_sl"), -1.0)
    quality_is_calibrated = (
        all(key in quality for key in ("expected_net_r", "uncertainty", "p_sl"))
        and 0.0 <= uncertainty <= 1.0
        and 0.0 <= p_sl <= 1.0
    )

    if not quality_is_calibrated:
        apply_limit(1.0, "UNVALIDATED_QUALITY_MODEL")
        confidence = "LOW"
    elif uncertainty > 0.20 or p_sl > 0.30 or expected_net_r <= 0:
        apply_limit(1.0, "QUALITY_UNCERTAINTY_LIMIT")
        confidence = "LOW"
    elif uncertainty > 0.12 or p_sl > 0.22:
        apply_limit(2.0, "QUALITY_UNCERTAINTY_LIMIT")
        confidence = "MEDIUM"
    else:
        confidence = "HIGH"

    if normalized_tier in {"A-", "A_MINUS"}:
        confidence = "LOW"

    # When even 1x at the reference margin fraction exceeds the risk budget,
    # reduce the suggested margin fraction rather than publishing an unsafe 1x.
    if cap < 1.0:
        adjusted_margin = risk_budget / effective_stop
        if adjusted_margin < MIN_REFERENCE_MARGIN_FRACTION:
            return None
        margin_fraction = min(margin_fraction, adjusted_margin)
        cap = 1.0
        constraint = "MARGIN_FRACTION_LIMIT"

    maximum = max(1, min(hard_cap, floor(cap)))
    recommended = maximum
    estimated_loss = margin_fraction * recommended * effective_stop

    while recommended > 1 and estimated_loss > risk_budget + 1e-12:
        recommended -= 1
        estimated_loss = margin_fraction * recommended * effective_stop
        constraint = "STOP_RISK_LIMIT"

    if estimated_loss > risk_budget + 1e-12:
        adjusted_margin = risk_budget / (recommended * effective_stop)
        if adjusted_margin < MIN_REFERENCE_MARGIN_FRACTION:
            return None
        margin_fraction = min(margin_fraction, adjusted_margin)
        estimated_loss = margin_fraction * recommended * effective_stop
        constraint = "MARGIN_FRACTION_LIMIT"

    maximum = max(recommended, min(maximum, floor(risk_budget / (margin_fraction * effective_stop))))

    return LeverageAdvice(
        recommended_multiple=recommended,
        maximum_multiple=maximum,
        suggested_margin_fraction=margin_fraction,
        estimated_reference_loss_fraction=estimated_loss,
        effective_stop_fraction=effective_stop,
        binding_constraint=constraint,
        confidence=confidence,
    )
