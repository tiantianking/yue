from __future__ import annotations

import numpy as np
import pandas as pd

from okx_signal_system.research.funding_carry_tilt import (
    ambiguous_side,
    carry_benefit,
    causal_recent_funding_mean,
    target_turnover,
)


def test_causal_recent_funding_mean_uses_only_known_settlements() -> None:
    times = pd.Series(pd.to_datetime([
        "2026-01-01T00:00:00Z",
        "2026-01-01T08:00:00Z",
        "2026-01-01T16:00:00Z",
        "2026-01-02T00:00:00Z",
    ]))
    rates = pd.Series([0.01, 0.02, 0.03, 0.50])
    value = causal_recent_funding_mean(
        times,
        rates,
        pd.Timestamp("2026-01-01T20:00:00Z"),
        settlements=3,
    )
    assert np.isclose(value, 0.02)


def test_carry_benefit_matches_position_direction() -> None:
    assert np.isclose(carry_benefit(1.0, 0.001), -0.001)
    assert np.isclose(carry_benefit(-1.0, 0.001), 0.001)


def test_ambiguity_and_target_turnover_helpers() -> None:
    assert ambiguous_side([0.1, 0.2, 0.2, 0.3])
    assert not ambiguous_side([0.1, 0.2, 0.3, 0.4])
    weights = np.asarray([[0.5, -0.5], [0.5, -0.5], [-0.5, 0.5]])
    assert np.isclose(target_turnover(weights), 4.0)
