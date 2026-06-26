from __future__ import annotations

from collections.abc import Mapping, Sequence

import numpy as np
import pandas as pd


def _ordered_finite(mapping: Mapping[str, float], symbols: Sequence[str], *, descending: bool) -> list[str]:
    series = (
        pd.Series(mapping, dtype=float)
        .reindex(list(symbols))
        .replace([np.inf, -np.inf], np.nan)
        .dropna()
        .sort_values(ascending=not descending, kind="mergesort")
    )
    return list(series.index)


def _validate(
    scores: Sequence[Mapping[str, float]],
    liquidity_scores: Sequence[Mapping[str, float]],
    symbols: Sequence[str],
    *,
    top_n: int,
    exit_rank: int,
    eligible_count: int,
) -> None:
    if len(scores) != len(liquidity_scores):
        raise ValueError("one liquidity mapping is required per momentum mapping")
    if not scores:
        raise ValueError("non-empty score paths are required")
    if len(set(symbols)) != len(symbols):
        raise ValueError("symbols must be unique")
    if top_n <= 0:
        raise ValueError("top_n must be positive")
    if exit_rank < top_n:
        raise ValueError("exit_rank must be at least top_n")
    if eligible_count < top_n * 2:
        raise ValueError("eligible_count must be large enough to fill both sides")
    if eligible_count > len(symbols):
        raise ValueError("eligible_count cannot exceed the universe size")


def liquidity_admission_hysteresis_weights(
    scores: Sequence[Mapping[str, float]],
    liquidity_scores: Sequence[Mapping[str, float]],
    symbols: Sequence[str],
    *,
    top_n: int = 4,
    exit_rank: int = 6,
    eligible_count: int = 12,
) -> np.ndarray:
    """Apply momentum hysteresis while restricting only new entries by trailing liquidity.

    Existing positions remain governed solely by the parent momentum exit rank.
    Liquidity may change who fills a vacancy, but it never forces an incumbent out.
    """

    _validate(
        scores,
        liquidity_scores,
        symbols,
        top_n=top_n,
        exit_rank=exit_rank,
        eligible_count=eligible_count,
    )
    ordered_symbols = list(symbols)
    lookup = {symbol: index for index, symbol in enumerate(ordered_symbols)}
    previous_longs: list[str] = []
    previous_shorts: list[str] = []
    output: list[np.ndarray] = []

    for score_mapping, liquidity_mapping in zip(scores, liquidity_scores, strict=True):
        descending = _ordered_finite(score_mapping, ordered_symbols, descending=True)
        if len(descending) < top_n * 2:
            raise ValueError("insufficient finite momentum scores")
        ascending = list(reversed(descending))
        liquid_descending = _ordered_finite(liquidity_mapping, ordered_symbols, descending=True)
        if len(liquid_descending) < eligible_count:
            raise ValueError("insufficient finite liquidity scores")
        eligible = set(liquid_descending[:eligible_count])

        long_rank = {symbol: rank + 1 for rank, symbol in enumerate(descending)}
        short_rank = {symbol: rank + 1 for rank, symbol in enumerate(ascending)}

        retained_longs = sorted(
            (symbol for symbol in previous_longs if long_rank.get(symbol, 10**9) <= exit_rank),
            key=long_rank.__getitem__,
        )
        long_candidates = retained_longs + [
            symbol for symbol in descending if symbol in eligible and symbol not in retained_longs
        ]
        longs = long_candidates[:top_n]
        if len(longs) != top_n:
            raise ValueError("liquidity admission could not fill the long side")

        retained_shorts = sorted(
            (
                symbol
                for symbol in previous_shorts
                if short_rank.get(symbol, 10**9) <= exit_rank and symbol not in longs
            ),
            key=short_rank.__getitem__,
        )
        short_candidates = retained_shorts + [
            symbol
            for symbol in ascending
            if symbol in eligible and symbol not in retained_shorts and symbol not in longs
        ]
        shorts = short_candidates[:top_n]
        if len(shorts) != top_n:
            raise ValueError("liquidity admission could not fill the short side")

        weights = np.zeros(len(ordered_symbols), dtype=float)
        for symbol in longs:
            weights[lookup[symbol]] = 0.5 / top_n
        for symbol in shorts:
            weights[lookup[symbol]] = -0.5 / top_n
        output.append(weights)
        previous_longs, previous_shorts = longs, shorts

    return np.asarray(output, dtype=float)


def eligible_set_fraction(
    liquidity_scores: Sequence[Mapping[str, float]],
    symbols: Sequence[str],
    *,
    eligible_count: int,
) -> float:
    """Return the fraction of rows with at least ``eligible_count`` finite liquidity values."""

    if not liquidity_scores:
        return 0.0
    complete = 0
    for mapping in liquidity_scores:
        finite = (
            pd.Series(mapping, dtype=float)
            .reindex(list(symbols))
            .replace([np.inf, -np.inf], np.nan)
            .notna()
            .sum()
        )
        complete += int(finite >= eligible_count)
    return complete / len(liquidity_scores)
