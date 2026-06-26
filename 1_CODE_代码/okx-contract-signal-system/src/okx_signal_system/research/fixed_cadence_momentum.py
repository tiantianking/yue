from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class FixedCadenceWeights:
    weights: np.ndarray
    refresh_flags: np.ndarray


def _ranked_members(
    mapping: Mapping[str, float],
    symbols: Sequence[str],
    *,
    top_n: int,
    exit_rank: int,
    previous_longs: Sequence[str],
    previous_shorts: Sequence[str],
) -> tuple[list[str], list[str]]:
    score = (
        pd.Series(mapping, dtype=float)
        .reindex(list(symbols))
        .replace([np.inf, -np.inf], np.nan)
        .dropna()
        .sort_values(ascending=False, kind="mergesort")
    )
    if len(score) < top_n * 2:
        raise ValueError("insufficient finite scores")

    descending = list(score.index)
    ascending = list(reversed(descending))
    long_rank = {symbol: rank + 1 for rank, symbol in enumerate(descending)}
    short_rank = {symbol: rank + 1 for rank, symbol in enumerate(ascending)}

    longs = [symbol for symbol in previous_longs if long_rank.get(symbol, 10**9) <= exit_rank]
    for symbol in descending:
        if symbol not in longs and len(longs) < top_n:
            longs.append(symbol)
        if len(longs) == top_n:
            break

    shorts = [
        symbol
        for symbol in previous_shorts
        if short_rank.get(symbol, 10**9) <= exit_rank and symbol not in longs
    ]
    for symbol in ascending:
        if symbol not in shorts and symbol not in longs and len(shorts) < top_n:
            shorts.append(symbol)
        if len(shorts) == top_n:
            break

    if len(longs) != top_n or len(shorts) != top_n:
        raise ValueError("unable to fill both portfolio sides")
    return longs, shorts


def fixed_cadence_hysteresis_weights(
    scores: Sequence[Mapping[str, float]],
    decision_times: Sequence[pd.Timestamp | str],
    symbols: Sequence[str],
    *,
    anchor_utc: pd.Timestamp | str,
    cadence_days: int = 3,
    top_n: int = 4,
    exit_rank: int = 6,
    gross_exposure: float = 1.0,
) -> FixedCadenceWeights:
    """Build a causal rank portfolio that refreshes only on a fixed UTC cadence.

    Scores are consumed only at timestamps that lie exactly on the frozen cadence.
    Between refresh timestamps, the previous target is carried unchanged. The
    function does not inspect returns or prices and therefore cannot introduce a
    future-return dependency.
    """

    if len(scores) != len(decision_times):
        raise ValueError("scores and decision_times must have equal length")
    if cadence_days <= 0:
        raise ValueError("cadence_days must be positive")
    if top_n <= 0:
        raise ValueError("top_n must be positive")
    if exit_rank < top_n:
        raise ValueError("exit_rank must be at least top_n")
    if not 0.0 < gross_exposure <= 1.0:
        raise ValueError("gross_exposure must be in (0, 1]")
    ordered_symbols = list(symbols)
    if len(ordered_symbols) != len(set(ordered_symbols)):
        raise ValueError("symbols must be unique")
    if len(ordered_symbols) < top_n * 2:
        raise ValueError("universe is too small")

    times = pd.DatetimeIndex(pd.to_datetime(list(decision_times), utc=True))
    if not times.is_monotonic_increasing or times.has_duplicates:
        raise ValueError("decision_times must be strictly increasing")
    anchor = pd.Timestamp(anchor_utc)
    anchor = anchor.tz_localize("UTC") if anchor.tzinfo is None else anchor.tz_convert("UTC")
    cadence = pd.Timedelta(days=cadence_days)

    lookup = {symbol: index for index, symbol in enumerate(ordered_symbols)}
    current = np.zeros(len(ordered_symbols), dtype=float)
    previous_longs: list[str] = []
    previous_shorts: list[str] = []
    output: list[np.ndarray] = []
    flags: list[bool] = []

    for mapping, timestamp in zip(scores, times, strict=True):
        delta = timestamp - anchor
        refresh = delta >= pd.Timedelta(0) and delta % cadence == pd.Timedelta(0)
        if refresh:
            previous_longs, previous_shorts = _ranked_members(
                mapping,
                ordered_symbols,
                top_n=top_n,
                exit_rank=exit_rank,
                previous_longs=previous_longs,
                previous_shorts=previous_shorts,
            )
            current = np.zeros(len(ordered_symbols), dtype=float)
            side_weight = gross_exposure / 2.0 / top_n
            for symbol in previous_longs:
                current[lookup[symbol]] = side_weight
            for symbol in previous_shorts:
                current[lookup[symbol]] = -side_weight
        output.append(current.copy())
        flags.append(bool(refresh))

    return FixedCadenceWeights(
        weights=np.asarray(output, dtype=float),
        refresh_flags=np.asarray(flags, dtype=bool),
    )


def next_refresh_at_or_after(
    timestamp_utc: pd.Timestamp | str,
    *,
    anchor_utc: pd.Timestamp | str,
    cadence_days: int,
) -> pd.Timestamp:
    if cadence_days <= 0:
        raise ValueError("cadence_days must be positive")
    timestamp = pd.Timestamp(timestamp_utc)
    timestamp = timestamp.tz_localize("UTC") if timestamp.tzinfo is None else timestamp.tz_convert("UTC")
    anchor = pd.Timestamp(anchor_utc)
    anchor = anchor.tz_localize("UTC") if anchor.tzinfo is None else anchor.tz_convert("UTC")
    if timestamp <= anchor:
        return anchor
    cadence = pd.Timedelta(days=cadence_days)
    periods = int(np.ceil((timestamp - anchor) / cadence))
    return anchor + periods * cadence
