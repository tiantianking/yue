from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from okx_signal_system.research.fixed_cadence_momentum import (
    fixed_cadence_hysteresis_weights,
    next_refresh_at_or_after,
    staggered_cadence_hysteresis_weights,
)


SYMBOLS = [f"S{index}" for index in range(8)]


def _score(order: list[str]) -> dict[str, float]:
    return {symbol: float(len(order) - index) for index, symbol in enumerate(order)}


def test_fixed_cadence_refreshes_only_on_frozen_dates() -> None:
    times = pd.date_range("2026-01-01T04:00:00Z", periods=8, freq="1D")
    scores = [_score(SYMBOLS) for _ in times]

    result = fixed_cadence_hysteresis_weights(
        scores,
        times,
        SYMBOLS,
        anchor_utc="2026-01-01T04:00:00Z",
        cadence_days=3,
        top_n=2,
        exit_rank=3,
        gross_exposure=0.4,
    )

    assert result.refresh_flags.tolist() == [True, False, False, True, False, False, True, False]
    assert np.allclose(result.weights[0], result.weights[1])
    assert np.allclose(result.weights[1], result.weights[2])
    assert np.isclose(np.abs(result.weights[0]).sum(), 0.4)
    assert np.isclose(result.weights[0].sum(), 0.0)


def test_interim_rank_changes_do_not_change_held_target() -> None:
    times = pd.date_range("2026-01-01T04:00:00Z", periods=4, freq="1D")
    first = _score(SYMBOLS)
    reversed_score = _score(list(reversed(SYMBOLS)))

    result = fixed_cadence_hysteresis_weights(
        [first, reversed_score, reversed_score, reversed_score],
        times,
        SYMBOLS,
        anchor_utc=times[0],
        cadence_days=3,
        top_n=2,
        exit_rank=3,
        gross_exposure=1.0,
    )

    assert np.array_equal(result.weights[0], result.weights[1])
    assert np.array_equal(result.weights[1], result.weights[2])
    assert not np.array_equal(result.weights[2], result.weights[3])


def test_hysteresis_retains_incumbent_at_exit_rank() -> None:
    times = pd.to_datetime(["2026-01-01T04:00:00Z", "2026-01-04T04:00:00Z"], utc=True)
    first_order = ["S0", "S1", "S2", "S3", "S4", "S5", "S6", "S7"]
    second_order = ["S2", "S1", "S0", "S3", "S4", "S5", "S6", "S7"]

    result = fixed_cadence_hysteresis_weights(
        [_score(first_order), _score(second_order)],
        times,
        SYMBOLS,
        anchor_utc=times[0],
        cadence_days=3,
        top_n=2,
        exit_rank=3,
        gross_exposure=1.0,
    )

    index = {symbol: position for position, symbol in enumerate(SYMBOLS)}
    assert result.weights[1, index["S0"]] > 0.0
    assert result.weights[1, index["S1"]] > 0.0
    assert result.weights[1, index["S2"]] == 0.0


def test_staggered_cohorts_equal_average_frozen_calendar_phases() -> None:
    times = pd.date_range("2026-01-01T04:00:00Z", periods=8, freq="1D")
    scores = [_score(SYMBOLS) for _ in times]
    individual = [
        fixed_cadence_hysteresis_weights(
            scores,
            times,
            SYMBOLS,
            anchor_utc=times[0] + pd.Timedelta(days=offset),
            cadence_days=3,
            top_n=2,
            exit_rank=3,
            gross_exposure=0.4,
        )
        for offset in (0, 1, 2)
    ]

    result = staggered_cadence_hysteresis_weights(
        scores,
        times,
        SYMBOLS,
        base_anchor_utc=times[0],
        cohort_offsets_days=(0, 1, 2),
        cadence_days=3,
        top_n=2,
        exit_rank=3,
        gross_exposure=0.4,
    )

    expected = np.mean(np.stack([item.weights for item in individual], axis=0), axis=0)
    assert np.allclose(result.weights, expected)
    assert result.refresh_flags.tolist() == [True] * len(times)
    assert np.isclose(np.abs(result.weights[2]).sum(), 0.4)
    assert np.isclose(result.weights[2].sum(), 0.0)


def test_staggered_cohorts_reject_duplicate_or_out_of_cycle_offsets() -> None:
    times = pd.date_range("2026-01-01T04:00:00Z", periods=2, freq="1D")
    scores = [_score(SYMBOLS) for _ in times]
    with pytest.raises(ValueError, match="unique"):
        staggered_cadence_hysteresis_weights(
            scores,
            times,
            SYMBOLS,
            base_anchor_utc=times[0],
            cohort_offsets_days=(0, 0),
            cadence_days=3,
        )
    with pytest.raises(ValueError, match="within"):
        staggered_cadence_hysteresis_weights(
            scores,
            times,
            SYMBOLS,
            base_anchor_utc=times[0],
            cohort_offsets_days=(0, 3),
            cadence_days=3,
        )


def test_next_refresh_uses_same_calendar_anchor() -> None:
    anchor = "2023-09-01T04:00:00Z"
    assert next_refresh_at_or_after(
        "2026-06-26T13:07:47Z",
        anchor_utc=anchor,
        cadence_days=3,
    ) == pd.Timestamp("2026-06-29T04:00:00Z")


def test_invalid_parameter_space_is_rejected() -> None:
    with pytest.raises(ValueError, match="exit_rank"):
        fixed_cadence_hysteresis_weights(
            [_score(SYMBOLS)],
            ["2026-01-01T04:00:00Z"],
            SYMBOLS,
            anchor_utc="2026-01-01T04:00:00Z",
            cadence_days=3,
            top_n=3,
            exit_rank=2,
        )
