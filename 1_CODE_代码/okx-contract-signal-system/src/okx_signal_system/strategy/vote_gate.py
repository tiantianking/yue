from __future__ import annotations

DEFAULT_MIN_VOTE_APPROVAL_RATE = 0.40


def min_vote_approval_rate(config: dict | None = None) -> float:
    strategy_cfg = (config or {}).get("strategy", {}) if isinstance(config, dict) else {}
    raw = strategy_cfg.get("min_vote_approval_rate", DEFAULT_MIN_VOTE_APPROVAL_RATE)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = DEFAULT_MIN_VOTE_APPROVAL_RATE
    return max(0.0, min(1.0, value))


def vote_gate_passed(final_side: str, signal_side: str, approval_rate: float, min_rate: float) -> bool:
    return final_side == signal_side and float(approval_rate) >= float(min_rate)
