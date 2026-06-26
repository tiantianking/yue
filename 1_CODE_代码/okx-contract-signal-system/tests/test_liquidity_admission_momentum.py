from __future__ import annotations

import numpy as np
import pytest

from okx_signal_system.research.liquidity_admission_momentum import (
    eligible_set_fraction,
    liquidity_admission_hysteresis_weights,
)


SYMBOLS = [f"S{i}" for i in range(8)]


def _mapping(order: list[str]) -> dict[str, float]:
    return {symbol: float(len(order) - rank) for rank, symbol in enumerate(order)}


def test_liquidity_restricts_new_entries_but_not_incumbent_retention() -> None:
    first_score = _mapping(SYMBOLS)
    second_score = _mapping(["S1", "S0", "S2", "S3", "S4", "S5", "S6", "S7"])
    first_liquidity = _mapping(SYMBOLS)
    second_liquidity = _mapping(["S1", "S2", "S3", "S4", "S5", "S6", "S7", "S0"])

    weights = liquidity_admission_hysteresis_weights(
        [first_score, second_score],
        [first_liquidity, second_liquidity],
        SYMBOLS,
        top_n=2,
        exit_rank=3,
        eligible_count=4,
    )

    assert weights.shape == (2, 8)
    assert weights[1, SYMBOLS.index("S0")] > 0.0
    assert np.allclose(weights.sum(axis=1), 0.0)
    assert np.allclose(np.abs(weights).sum(axis=1), 1.0)


def test_vacancy_is_filled_only_from_liquidity_eligible_set() -> None:
    first_score = _mapping(SYMBOLS)
    second_score = _mapping(["S2", "S3", "S4", "S5", "S0", "S1", "S6", "S7"])
    first_liquidity = _mapping(SYMBOLS)
    second_liquidity = _mapping(["S3", "S4", "S5", "S6", "S0", "S1", "S2", "S7"])

    weights = liquidity_admission_hysteresis_weights(
        [first_score, second_score],
        [first_liquidity, second_liquidity],
        SYMBOLS,
        top_n=2,
        exit_rank=2,
        eligible_count=4,
    )

    selected_longs = {SYMBOLS[index] for index in np.flatnonzero(weights[1] > 0.0)}
    assert selected_longs == {"S3", "S4"}
    assert "S2" not in selected_longs


def test_weights_are_deterministic_under_ties() -> None:
    score = {symbol: 1.0 for symbol in SYMBOLS}
    liquidity = {symbol: 1.0 for symbol in SYMBOLS}
    first = liquidity_admission_hysteresis_weights(
        [score], [liquidity], SYMBOLS, top_n=2, exit_rank=3, eligible_count=6
    )
    second = liquidity_admission_hysteresis_weights(
        [score], [liquidity], SYMBOLS, top_n=2, exit_rank=3, eligible_count=6
    )
    assert np.array_equal(first, second)


def test_eligible_set_fraction_counts_complete_rows() -> None:
    rows = [
        {symbol: float(index) for index, symbol in enumerate(SYMBOLS)},
        {symbol: (float(index) if index < 5 else float("nan")) for index, symbol in enumerate(SYMBOLS)},
    ]
    assert eligible_set_fraction(rows, SYMBOLS, eligible_count=6) == pytest.approx(0.5)


def test_invalid_eligible_count_is_rejected() -> None:
    score = _mapping(SYMBOLS)
    with pytest.raises(ValueError, match="large enough"):
        liquidity_admission_hysteresis_weights(
            [score], [score], SYMBOLS, top_n=2, exit_rank=3, eligible_count=3
        )
