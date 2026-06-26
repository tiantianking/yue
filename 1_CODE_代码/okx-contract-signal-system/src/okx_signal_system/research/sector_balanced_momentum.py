from __future__ import annotations

from collections.abc import Mapping, Sequence

import numpy as np
import pandas as pd


def _validate_inputs(
    symbols: Sequence[str],
    sector_by_symbol: Mapping[str, str],
    *,
    top_n: int,
    max_per_sector: int,
) -> None:
    if top_n <= 0:
        raise ValueError("top_n must be positive")
    if max_per_sector <= 0:
        raise ValueError("max_per_sector must be positive")
    missing = [symbol for symbol in symbols if symbol not in sector_by_symbol]
    if missing:
        raise ValueError(f"missing sector mapping for: {missing}")
    available_sectors = {sector_by_symbol[symbol] for symbol in symbols}
    if len(available_sectors) * max_per_sector < top_n:
        raise ValueError("sector cap cannot fill the requested side")


def _select_with_cap(
    ranked_symbols: Sequence[str],
    sector_by_symbol: Mapping[str, str],
    *,
    count: int,
    max_per_sector: int,
    excluded: set[str] | None = None,
) -> list[str]:
    selected: list[str] = []
    sector_counts: dict[str, int] = {}
    blocked = excluded or set()
    for symbol in ranked_symbols:
        if symbol in blocked:
            continue
        sector = sector_by_symbol[symbol]
        if sector_counts.get(sector, 0) >= max_per_sector:
            continue
        selected.append(symbol)
        sector_counts[sector] = sector_counts.get(sector, 0) + 1
        if len(selected) == count:
            break
    if len(selected) != count:
        raise ValueError("insufficient eligible symbols after applying sector cap")
    return selected


def sector_capped_rank_weights(
    score: pd.Series,
    symbols: Sequence[str],
    sector_by_symbol: Mapping[str, str],
    *,
    top_n: int = 4,
    max_per_sector: int = 2,
) -> tuple[np.ndarray, list[str], list[str]]:
    """Build an equal-weight market-neutral rank portfolio with a per-side sector cap."""

    _validate_inputs(symbols, sector_by_symbol, top_n=top_n, max_per_sector=max_per_sector)
    ordered_symbols = list(symbols)
    clean = score.reindex(ordered_symbols).replace([np.inf, -np.inf], np.nan).dropna()
    if len(clean) < top_n * 2:
        raise ValueError("insufficient finite scores")

    descending = list(clean.sort_values(ascending=False, kind="mergesort").index)
    ascending = list(reversed(descending))
    longs = _select_with_cap(
        descending,
        sector_by_symbol,
        count=top_n,
        max_per_sector=max_per_sector,
    )
    shorts = _select_with_cap(
        ascending,
        sector_by_symbol,
        count=top_n,
        max_per_sector=max_per_sector,
        excluded=set(longs),
    )

    lookup = {symbol: index for index, symbol in enumerate(ordered_symbols)}
    weights = np.zeros(len(ordered_symbols), dtype=float)
    for symbol in longs:
        weights[lookup[symbol]] = 0.5 / top_n
    for symbol in shorts:
        weights[lookup[symbol]] = -0.5 / top_n
    return weights, longs, shorts


def sector_capped_hysteresis_weights(
    scores: Sequence[Mapping[str, float]],
    symbols: Sequence[str],
    sector_by_symbol: Mapping[str, str],
    *,
    top_n: int = 4,
    exit_rank: int = 6,
    max_per_sector: int = 2,
) -> np.ndarray:
    """Apply the frozen top-N entry/exit-rank hysteresis while respecting sector caps."""

    _validate_inputs(symbols, sector_by_symbol, top_n=top_n, max_per_sector=max_per_sector)
    if exit_rank < top_n:
        raise ValueError("exit_rank must be at least top_n")

    ordered_symbols = list(symbols)
    lookup = {symbol: index for index, symbol in enumerate(ordered_symbols)}
    previous_longs: list[str] = []
    previous_shorts: list[str] = []
    output: list[np.ndarray] = []

    for mapping in scores:
        score = (
            pd.Series(mapping, dtype=float)
            .reindex(ordered_symbols)
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

        retained_longs = sorted(
            (symbol for symbol in previous_longs if long_rank.get(symbol, 10**9) <= exit_rank),
            key=long_rank.__getitem__,
        )
        longs = _select_with_cap(
            retained_longs + [symbol for symbol in descending if symbol not in retained_longs],
            sector_by_symbol,
            count=top_n,
            max_per_sector=max_per_sector,
        )

        retained_shorts = sorted(
            (
                symbol
                for symbol in previous_shorts
                if short_rank.get(symbol, 10**9) <= exit_rank and symbol not in longs
            ),
            key=short_rank.__getitem__,
        )
        shorts = _select_with_cap(
            retained_shorts + [symbol for symbol in ascending if symbol not in retained_shorts],
            sector_by_symbol,
            count=top_n,
            max_per_sector=max_per_sector,
            excluded=set(longs),
        )

        weights = np.zeros(len(ordered_symbols), dtype=float)
        for symbol in longs:
            weights[lookup[symbol]] = 0.5 / top_n
        for symbol in shorts:
            weights[lookup[symbol]] = -0.5 / top_n
        output.append(weights)
        previous_longs, previous_shorts = longs, shorts

    return np.asarray(output, dtype=float)


def maximum_symbol_slot_share(weights: np.ndarray) -> dict[str, float]:
    matrix = np.asarray(weights, dtype=float)
    if matrix.ndim != 2 or matrix.shape[0] == 0:
        raise ValueError("non-empty two-dimensional weights required")
    positive_slots = np.count_nonzero(matrix > 0.0)
    negative_slots = np.count_nonzero(matrix < 0.0)
    long_share = float(np.max(np.count_nonzero(matrix > 0.0, axis=0)) / positive_slots)
    short_share = float(np.max(np.count_nonzero(matrix < 0.0, axis=0)) / negative_slots)
    return {"long": long_share, "short": short_share, "maximum": max(long_share, short_share)}


def maximum_sector_slot_share(
    weights: np.ndarray,
    symbols: Sequence[str],
    sector_by_symbol: Mapping[str, str],
) -> dict[str, float]:
    matrix = np.asarray(weights, dtype=float)
    if matrix.ndim != 2 or matrix.shape[0] == 0:
        raise ValueError("non-empty two-dimensional weights required")
    sectors = sorted({sector_by_symbol[symbol] for symbol in symbols})
    long_total = np.count_nonzero(matrix > 0.0)
    short_total = np.count_nonzero(matrix < 0.0)
    long_max = 0.0
    short_max = 0.0
    for sector in sectors:
        columns = [index for index, symbol in enumerate(symbols) if sector_by_symbol[symbol] == sector]
        long_max = max(long_max, float(np.count_nonzero(matrix[:, columns] > 0.0) / long_total))
        short_max = max(short_max, float(np.count_nonzero(matrix[:, columns] < 0.0) / short_total))
    return {"long": long_max, "short": short_max, "maximum": max(long_max, short_max)}


def maximum_loss_streak(values: Sequence[float]) -> int:
    longest = 0
    current = 0
    for value in values:
        if float(value) < 0.0:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest
