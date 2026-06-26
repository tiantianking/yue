from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from okx_signal_system.research.sector_balanced_momentum import (
    maximum_loss_streak,
    maximum_sector_slot_share,
    maximum_symbol_slot_share,
    sector_capped_hysteresis_weights,
    sector_capped_rank_weights,
)

SYMBOLS = ["A", "B", "C", "D", "E", "F", "G", "H", "I"]
SECTORS = {
    "A": "S1",
    "B": "S1",
    "C": "S1",
    "D": "S2",
    "E": "S2",
    "F": "S2",
    "G": "S3",
    "H": "S3",
    "I": "S3",
}


def test_sector_capped_rank_weights_are_neutral_and_respect_cap() -> None:
    score = pd.Series({symbol: float(20 - index) for index, symbol in enumerate(SYMBOLS)})
    weights, longs, shorts = sector_capped_rank_weights(
        score,
        SYMBOLS,
        SECTORS,
        top_n=4,
        max_per_sector=2,
    )

    assert len(longs) == 4
    assert len(shorts) == 4
    assert set(longs).isdisjoint(shorts)
    assert np.isclose(weights.sum(), 0.0)
    assert np.isclose(np.abs(weights).sum(), 1.0)
    for side in (longs, shorts):
        counts = {sector: sum(SECTORS[symbol] == sector for symbol in side) for sector in set(SECTORS.values())}
        assert max(counts.values()) <= 2


def test_sector_capped_hysteresis_keeps_eligible_incumbent_without_breaking_cap() -> None:
    first_order = ["A", "D", "G", "B", "E", "H", "C", "F", "I"]
    second_order = ["C", "D", "G", "A", "E", "H", "B", "F", "I"]
    first = {symbol: float(len(first_order) - index) for index, symbol in enumerate(first_order)}
    second = {symbol: float(len(second_order) - index) for index, symbol in enumerate(second_order)}

    weights = sector_capped_hysteresis_weights(
        [first, second],
        SYMBOLS,
        SECTORS,
        top_n=4,
        exit_rank=6,
        max_per_sector=2,
    )

    incumbent_index = SYMBOLS.index("A")
    assert weights[0, incumbent_index] > 0.0
    assert weights[1, incumbent_index] > 0.0
    assert np.allclose(weights.sum(axis=1), 0.0)
    assert np.allclose(np.abs(weights).sum(axis=1), 1.0)
    assert maximum_sector_slot_share(weights, SYMBOLS, SECTORS)["maximum"] <= 0.5


def test_impossible_sector_cap_fails_closed() -> None:
    score = pd.Series({symbol: float(index) for index, symbol in enumerate(SYMBOLS)})
    with pytest.raises(ValueError, match="cannot fill"):
        sector_capped_rank_weights(
            score,
            SYMBOLS,
            SECTORS,
            top_n=4,
            max_per_sector=1,
        )


def test_concentration_and_loss_streak_helpers() -> None:
    weights = np.zeros((4, len(SYMBOLS)), dtype=float)
    weights[:, :4] = 0.125
    weights[:, 4:8] = -0.125
    symbol_share = maximum_symbol_slot_share(weights)
    assert np.isclose(symbol_share["long"], 0.25)
    assert np.isclose(symbol_share["short"], 0.25)
    assert maximum_loss_streak([0.1, -0.1, -0.2, 0.0, -0.3, -0.4, -0.5]) == 3
