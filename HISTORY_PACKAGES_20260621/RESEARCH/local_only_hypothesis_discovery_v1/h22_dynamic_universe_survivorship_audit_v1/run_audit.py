from __future__ import annotations

import hashlib
import json
import math
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd
import pyarrow.dataset as ds

HERE = Path(__file__).resolve().parent
DISCOVERY_ROOT = HERE.parent
WORKSPACE_ROOT = HERE.parents[3]
if str(DISCOVERY_ROOT) not in sys.path:
    sys.path.insert(0, str(DISCOVERY_ROOT))

import momentum_overlay_common as common
PROTOCOL_PATH = HERE / "PROTOCOL_LOCKED_BEFORE_RESULTS.json"
RESULT_PATH = HERE / "RESULT.json"
REPORT_PATH = HERE / "RESULTS_CN.md"
HASHES_PATH = HERE / "HASHES.txt"
DATASET_ROOT = (
    WORKSPACE_ROOT
    / "历史数据_保留"
    / "lightweight_history"
    / "okx_dynamic_universe_4h_20230701_20260616_v1"
)
DATASET_MANIFEST_PATH = DATASET_ROOT / "DATASET_MANIFEST.json"
QUALITY_REPORT_PATH = DATASET_ROOT / "DATA_QUALITY_REPORT.json"
PARENT_PROTOCOL_PATH = (
    WORKSPACE_ROOT
    / "1_CODE_代码"
    / "okx-contract-signal-system"
    / "config"
    / "research_protocols"
    / "momentum_staggered_3x3_refresh_v1.json"
)


@dataclass(frozen=True)
class WeightBuildResult:
    weights: np.ndarray
    refresh_flags: np.ndarray
    forced_exit_counts: np.ndarray
    cohort_forced_exits: dict[str, int]


@dataclass(frozen=True)
class SimulationResult:
    frame: pd.DataFrame
    symbol_contributions: dict[str, float]
    terminal_exit_count: int
    terminal_exit_symbols: dict[str, int]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON object required: {path}")
    return payload


def _json_default(value: Any) -> Any:
    if isinstance(value, (pd.Timestamp, np.datetime64)):
        return pd.Timestamp(value).isoformat()
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        value = float(value)
        return value if math.isfinite(value) else None
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    raise TypeError(f"unsupported JSON value: {type(value)!r}")


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _atomic_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    os.replace(temporary, path)


def _validate_protocol(protocol: dict[str, Any]) -> None:
    if protocol.get("status") != "LOCKED_BEFORE_RESULTS":
        raise ValueError("protocol must be locked before results")
    if protocol.get("protocol_id") != "H22_DYNAMIC_UNIVERSE_SURVIVORSHIP_AUDIT_V1":
        raise ValueError("unexpected protocol id")
    signal = protocol["frozen_parent_signal"]
    frozen = (
        int(signal["formation_bars_4h"]),
        int(signal["signal_hour_utc"]),
        int(signal["entry_delay_hours"]),
        int(signal["entry_rank"]),
        int(signal["exit_rank"]),
        tuple(int(item) for item in signal["cohort_offsets_days"]),
        int(signal["cohort_refresh_days"]),
        float(signal["research_gross_exposure"]),
        int(signal["warmup_entries_excluded"]),
    )
    if frozen != (84, 0, 4, 4, 6, (0, 1, 2), 3, 0.4, 2):
        raise ValueError(f"frozen H22 constants changed: {frozen}")
    universe = protocol["point_in_time_universe"]
    if int(universe["minimum_consecutive_closed_bars"]) != 85:
        raise ValueError("minimum history must remain 85 bars")
    if int(universe["matched_universe_size"]) != 18:
        raise ValueError("dynamic universe size must remain 18")
    fixed = [str(item) for item in protocol["comparison_panels"]["fixed_survivor_panel"]]
    if len(fixed) != 18 or len(set(fixed)) != 18:
        raise ValueError("fixed survivor panel must contain 18 unique instruments")


def _validate_inputs(protocol: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    if _sha256(PARENT_PROTOCOL_PATH) != str(protocol["parent_protocol_sha256"]):
        raise ValueError("parent protocol hash mismatch")
    if _sha256(DATASET_MANIFEST_PATH) != str(protocol["dataset"]["dataset_manifest_sha256"]):
        raise ValueError("dataset manifest hash mismatch")
    manifest = _read_json(DATASET_MANIFEST_PATH)
    quality = _read_json(QUALITY_REPORT_PATH)
    if manifest.get("status") != "COMPLETE_VALIDATED":
        raise ValueError("dataset is not fully validated")
    if quality.get("status") != str(protocol["dataset"]["quality_status_required"]):
        raise ValueError("dataset quality report did not pass")
    if quality.get("failures"):
        raise ValueError("dataset quality report contains hard failures")
    return manifest, quality


def _load_panels() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, list[str]]:
    dataset = ds.dataset(DATASET_ROOT / "data", format="parquet", partitioning="hive")
    table = dataset.to_table(
        columns=["instrument_name", "bar_open_ms", "open", "close", "vol_quote"]
    )
    frame = table.to_pandas()
    frame["instrument_name"] = frame["instrument_name"].astype(str)
    frame["ts"] = pd.to_datetime(frame["bar_open_ms"], unit="ms", utc=True)
    for column in ("open", "close", "vol_quote"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    if frame.duplicated(["instrument_name", "ts"]).any():
        raise ValueError("duplicate instrument/timestamp rows in validated dataset")
    symbols = sorted(frame["instrument_name"].unique().tolist())
    index = pd.date_range(frame["ts"].min(), frame["ts"].max(), freq="4h", tz="UTC")

    def pivot(field: str) -> pd.DataFrame:
        output = frame.pivot(index="ts", columns="instrument_name", values=field)
        output = output.reindex(index=index, columns=symbols)
        output.index.name = "ts"
        output.columns.name = None
        return output.astype(float)

    return pivot("open"), pivot("close"), pivot("vol_quote"), symbols


def _load_parent_fixed_panels(
    fixed_symbols: list[str],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    panels = common.load_panels()
    missing = sorted(set(fixed_symbols) - set(panels.h4_open.columns))
    if missing:
        raise ValueError(f"fixed symbols absent from parent H22 panels: {missing}")
    return (
        panels.h4_open.loc[:, fixed_symbols].copy(),
        panels.h4_close.loc[:, fixed_symbols].copy(),
    )


def _build_signal_inputs(
    protocol: dict[str, Any],
    open_prices: pd.DataFrame,
    close_prices: pd.DataFrame,
    quote_volume: pd.DataFrame,
    symbols: list[str],
    parent_open_prices: pd.DataFrame,
    parent_close_prices: pd.DataFrame,
) -> dict[str, Any]:
    signal_spec = protocol["frozen_parent_signal"]
    universe_spec = protocol["point_in_time_universe"]
    lookback = int(signal_spec["formation_bars_4h"])
    required = int(universe_spec["minimum_consecutive_closed_bars"])
    dynamic_size = int(universe_spec["matched_universe_size"])
    excluded = set(str(item) for item in universe_spec["stablecoin_and_pegged_exclusions"])
    fixed_symbols = [str(item) for item in protocol["comparison_panels"]["fixed_survivor_panel"]]
    missing_fixed = sorted(set(fixed_symbols) - set(symbols))
    if missing_fixed:
        raise ValueError(f"fixed symbols absent from dynamic dataset: {missing_fixed}")

    score = close_prices / close_prices.shift(lookback) - 1.0
    parent_score = parent_close_prices / parent_close_prices.shift(lookback) - 1.0
    complete_history = close_prices.notna().rolling(required, min_periods=required).sum().eq(required)
    trailing_volume = quote_volume.rolling(lookback, min_periods=lookback).sum()

    first_entry = pd.Timestamp(protocol["history"]["first_signal_entry_utc"])
    last_entry = pd.Timestamp(protocol["history"]["last_available_entry_utc"])
    signal_hour = int(signal_spec["signal_hour_utc"])
    entry_delay = pd.Timedelta(hours=int(signal_spec["entry_delay_hours"]))

    entries: list[pd.Timestamp] = []
    fixed_mappings: list[dict[str, float]] = []
    dynamic_mappings: list[dict[str, float]] = []
    dynamic_universes: list[list[str]] = []
    dynamic_volumes: list[dict[str, float]] = []
    eligible_counts: list[int] = []

    for signal_time in score.index[score.index.hour == signal_hour]:
        entry = pd.Timestamp(signal_time) + entry_delay
        if entry < first_entry or entry > last_entry or entry not in open_prices.index:
            continue

        current_score = score.loc[signal_time].replace([np.inf, -np.inf], np.nan)
        current_volume = trailing_volume.loc[signal_time].replace([np.inf, -np.inf], np.nan)
        current_complete = complete_history.loc[signal_time]
        entry_open = open_prices.loc[entry]

        if signal_time not in parent_score.index or entry not in parent_open_prices.index:
            raise ValueError(f"parent fixed panel timeline missing at signal {signal_time} / entry {entry}")
        fixed_current = parent_score.loc[signal_time].reindex(fixed_symbols)
        fixed_entry_open = parent_open_prices.loc[entry].reindex(fixed_symbols)
        fixed_ready = fixed_current.notna() & fixed_entry_open.notna() & fixed_entry_open.gt(0.0)
        if not bool(fixed_ready.all()):
            missing = list(fixed_ready.index[~fixed_ready])
            raise ValueError(f"parent fixed panel not causally ready at {entry}: {missing}")

        candidates = pd.DataFrame(
            {
                "symbol": symbols,
                "score": current_score.reindex(symbols).to_numpy(dtype=float),
                "volume": current_volume.reindex(symbols).to_numpy(dtype=float),
                "complete": current_complete.reindex(symbols).fillna(False).to_numpy(dtype=bool),
                "entry_open": entry_open.reindex(symbols).to_numpy(dtype=float),
            }
        )
        candidates = candidates.loc[
            candidates["complete"]
            & np.isfinite(candidates["score"])
            & np.isfinite(candidates["volume"])
            & np.isfinite(candidates["entry_open"])
            & candidates["entry_open"].gt(0.0)
            & ~candidates["symbol"].isin(excluded)
        ].copy()
        candidates = candidates.sort_values(
            ["volume", "symbol"], ascending=[False, True], kind="mergesort"
        )
        eligible_counts.append(int(len(candidates)))
        if len(candidates) < dynamic_size:
            raise ValueError(f"fewer than {dynamic_size} eligible symbols at {entry}")
        selected = candidates.head(dynamic_size)
        selected_symbols = selected["symbol"].astype(str).tolist()

        entries.append(entry)
        fixed_mappings.append({symbol: float(fixed_current[symbol]) for symbol in fixed_symbols})
        dynamic_mappings.append(
            {row.symbol: float(row.score) for row in selected.itertuples(index=False)}
        )
        dynamic_universes.append(selected_symbols)
        dynamic_volumes.append(
            {row.symbol: float(row.volume) for row in selected.itertuples(index=False)}
        )

    if len(entries) < 10:
        raise ValueError("insufficient daily entries")
    spacing = pd.Series(entries).diff().dropna()
    if not spacing.eq(pd.Timedelta(days=1)).all():
        raise ValueError("entry timeline is not a complete daily sequence")

    return {
        "entries": entries,
        "fixed_mappings": fixed_mappings,
        "dynamic_mappings": dynamic_mappings,
        "dynamic_universes": dynamic_universes,
        "dynamic_volumes": dynamic_volumes,
        "eligible_counts": eligible_counts,
        "fixed_symbols": fixed_symbols,
    }


def _ranked_members(
    mapping: Mapping[str, float],
    *,
    top_n: int,
    exit_rank: int,
    previous_longs: Sequence[str],
    previous_shorts: Sequence[str],
) -> tuple[list[str], list[str]]:
    score = (
        pd.Series(mapping, dtype=float)
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
        raise ValueError("unable to fill both sides")
    return longs, shorts


def _single_cohort_weights(
    mappings: Sequence[Mapping[str, float]],
    decision_times: Sequence[pd.Timestamp],
    symbols: list[str],
    *,
    anchor: pd.Timestamp,
    cadence_days: int,
    top_n: int,
    exit_rank: int,
    gross_exposure: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, int]:
    lookup = {symbol: index for index, symbol in enumerate(symbols)}
    current_longs: list[str] = []
    current_shorts: list[str] = []
    weights: list[np.ndarray] = []
    refresh_flags: list[bool] = []
    forced_exit_counts: list[int] = []
    total_forced = 0
    side_weight = gross_exposure / 2.0 / top_n
    cadence = pd.Timedelta(days=cadence_days)

    for mapping, timestamp in zip(mappings, decision_times, strict=True):
        eligible = set(mapping)
        before = len(current_longs) + len(current_shorts)
        current_longs = [symbol for symbol in current_longs if symbol in eligible]
        current_shorts = [symbol for symbol in current_shorts if symbol in eligible]
        forced = before - len(current_longs) - len(current_shorts)
        total_forced += forced

        delta = timestamp - anchor
        refresh = delta >= pd.Timedelta(0) and delta % cadence == pd.Timedelta(0)
        if refresh:
            current_longs, current_shorts = _ranked_members(
                mapping,
                top_n=top_n,
                exit_rank=exit_rank,
                previous_longs=current_longs,
                previous_shorts=current_shorts,
            )

        row = np.zeros(len(symbols), dtype=float)
        for symbol in current_longs:
            row[lookup[symbol]] = side_weight
        for symbol in current_shorts:
            row[lookup[symbol]] = -side_weight
        weights.append(row)
        refresh_flags.append(bool(refresh))
        forced_exit_counts.append(int(forced))

    return (
        np.asarray(weights, dtype=float),
        np.asarray(refresh_flags, dtype=bool),
        np.asarray(forced_exit_counts, dtype=int),
        total_forced,
    )


def _staggered_weights(
    protocol: dict[str, Any],
    mappings: Sequence[Mapping[str, float]],
    entries: Sequence[pd.Timestamp],
    symbols: list[str],
) -> WeightBuildResult:
    signal = protocol["frozen_parent_signal"]
    base_anchor = pd.Timestamp(signal["base_anchor_utc"])
    cohorts = []
    cohort_forced: dict[str, int] = {}
    for offset in signal["cohort_offsets_days"]:
        offset_int = int(offset)
        built = _single_cohort_weights(
            mappings,
            entries,
            symbols,
            anchor=base_anchor + pd.Timedelta(days=offset_int),
            cadence_days=int(signal["cohort_refresh_days"]),
            top_n=int(signal["entry_rank"]),
            exit_rank=int(signal["exit_rank"]),
            gross_exposure=float(signal["research_gross_exposure"]),
        )
        cohorts.append(built)
        cohort_forced[str(offset_int)] = int(built[3])
    weights = np.mean(np.stack([item[0] for item in cohorts], axis=0), axis=0)
    refresh_flags = np.any(np.stack([item[1] for item in cohorts], axis=0), axis=0)
    forced_exit_counts = np.sum(np.stack([item[2] for item in cohorts], axis=0), axis=0)
    return WeightBuildResult(
        weights=weights,
        refresh_flags=refresh_flags,
        forced_exit_counts=forced_exit_counts,
        cohort_forced_exits=cohort_forced,
    )


def _last_close_before(
    close_prices: pd.DataFrame,
    symbol: str,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> float | None:
    values = close_prices.loc[(close_prices.index >= start) & (close_prices.index < end), symbol]
    values = values.dropna()
    if values.empty:
        return None
    value = float(values.iloc[-1])
    return value if math.isfinite(value) and value > 0.0 else None


def _simulate(
    entries: list[pd.Timestamp],
    targets: np.ndarray,
    symbols: list[str],
    open_prices: pd.DataFrame,
    close_prices: pd.DataFrame,
    *,
    one_way_cost: float,
    funding_reserve_full_gross: float,
) -> SimulationResult:
    if len(entries) < 2 or targets.shape != (len(entries), len(symbols)):
        raise ValueError("simulation dimensions are invalid")
    pending_turnover = np.abs(targets[0]).astype(float)
    rows: list[dict[str, Any]] = []
    symbol_contributions = np.zeros(len(symbols), dtype=float)
    terminal_exit_symbols: dict[str, int] = {}
    terminal_exit_count = 0
    last_drifted = np.zeros(len(symbols), dtype=float)

    for index, (start, end) in enumerate(zip(entries[:-1], entries[1:])):
        weights = targets[index].astype(float)
        next_target = targets[index + 1].astype(float)
        active = np.abs(weights) > 1e-15
        start_px = open_prices.loc[start, symbols].to_numpy(dtype=float)
        end_px = open_prices.loc[end, symbols].to_numpy(dtype=float)
        if np.any(active & (~np.isfinite(start_px) | (start_px <= 0.0))):
            bad = [symbols[i] for i in np.flatnonzero(active & (~np.isfinite(start_px) | (start_px <= 0.0)))]
            raise ValueError(f"active position lacks valid start open at {start}: {bad}")

        returns = np.zeros(len(symbols), dtype=float)
        regular = active & np.isfinite(end_px) & (end_px > 0.0)
        returns[regular] = end_px[regular] / start_px[regular] - 1.0
        terminal = active & ~regular
        for column in np.flatnonzero(terminal):
            symbol = symbols[column]
            terminal_price = _last_close_before(close_prices, symbol, start, end)
            if terminal_price is None:
                raise ValueError(f"no causal terminal close for {symbol} during {start} to {end}")
            returns[column] = terminal_price / start_px[column] - 1.0
            terminal_exit_count += 1
            terminal_exit_symbols[symbol] = terminal_exit_symbols.get(symbol, 0) + 1

        gross_components = weights * returns
        transaction_components = -pending_turnover * one_way_cost
        reserve_components = -np.abs(weights) * funding_reserve_full_gross
        gross = float(gross_components.sum())
        equity_factor = 1.0 + gross
        if equity_factor <= 0.0:
            drifted = weights.copy()
        else:
            drifted = weights * (1.0 + returns) / equity_factor

        terminal_turnover = np.zeros(len(symbols), dtype=float)
        if np.any(terminal):
            terminal_turnover[terminal] = np.abs(drifted[terminal])
            drifted[terminal] = 0.0
        terminal_cost_components = -terminal_turnover * one_way_cost
        net_components = (
            gross_components
            + transaction_components
            + reserve_components
            + terminal_cost_components
        )
        net = float(net_components.sum())
        turnover = float(pending_turnover.sum() + terminal_turnover.sum())
        next_turnover = np.abs(next_target - drifted)
        symbol_contributions += net_components
        rows.append(
            {
                "start_utc": start,
                "end_utc": end,
                "gross_return": gross,
                "transaction_cost": float(-transaction_components.sum() - terminal_cost_components.sum()),
                "funding_reserve": float(-reserve_components.sum()),
                "net_return": net,
                "turnover": turnover,
                "gross_exposure": float(np.abs(weights).sum()),
                "terminal_exit_count": int(terminal.sum()),
            }
        )
        pending_turnover = next_turnover
        last_drifted = drifted

    if rows:
        final_turnover = np.abs(last_drifted)
        final_cost_components = -final_turnover * one_way_cost
        rows[-1]["transaction_cost"] += float(final_turnover.sum() * one_way_cost)
        rows[-1]["net_return"] += float(final_cost_components.sum())
        rows[-1]["turnover"] += float(final_turnover.sum())
        symbol_contributions += final_cost_components

    return SimulationResult(
        frame=pd.DataFrame(rows),
        symbol_contributions={symbol: float(symbol_contributions[i]) for i, symbol in enumerate(symbols)},
        terminal_exit_count=terminal_exit_count,
        terminal_exit_symbols=dict(sorted(terminal_exit_symbols.items())),
    )


def _profit_factor(values: pd.Series) -> float | None:
    positive = float(values[values > 0.0].sum())
    negative = float(-values[values < 0.0].sum())
    if negative == 0.0:
        return None if positive == 0.0 else float("inf")
    return positive / negative


def _maximum_loss_streak(values: np.ndarray) -> int:
    best = current = 0
    for value in values:
        if value < 0.0:
            current += 1
            best = max(best, current)
        else:
            current = 0
    return best


def _metrics(frame: pd.DataFrame) -> dict[str, Any]:
    if frame.empty:
        return {"periods": 0}
    values = frame["net_return"].to_numpy(dtype=float)
    equity = np.cumprod(1.0 + values)
    peaks = np.maximum.accumulate(np.concatenate([[1.0], equity]))[1:]
    drawdown = equity / peaks - 1.0
    gains = values[values > 0.0]
    losses = values[values < 0.0]
    return {
        "periods": int(len(frame)),
        "net_r": float(values.sum()),
        "mean": float(values.mean()),
        "profit_factor": _profit_factor(frame["net_return"]),
        "win_rate": float(np.mean(values > 0.0)),
        "payoff_ratio": float(gains.mean() / -losses.mean()) if len(gains) and len(losses) else None,
        "total_return": float(equity[-1] - 1.0),
        "maximum_drawdown": float(drawdown.min()),
        "maximum_loss_streak": _maximum_loss_streak(values),
        "mean_turnover": float(frame["turnover"].mean()),
        "transaction_cost": float(frame["transaction_cost"].sum()),
        "funding_reserve": float(frame["funding_reserve"].sum()),
        "mean_gross_exposure": float(frame["gross_exposure"].mean()),
        "terminal_exit_count": int(frame["terminal_exit_count"].sum()),
    }


def _segment_metrics(frame: pd.DataFrame, protocol: dict[str, Any]) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    for spec in protocol["history"]["segments"]:
        subset = frame.loc[
            (frame["start_utc"] >= pd.Timestamp(spec["start_utc"]))
            & (frame["start_utc"] < pd.Timestamp(spec["end_utc_exclusive"]))
        ]
        output[str(spec["name"])] = _metrics(subset)
    return output


def _positive_segment_count(segments: dict[str, dict[str, Any]]) -> int:
    return sum(float(item.get("total_return") or 0.0) > 0.0 for item in segments.values())


def _positive_share(values: Mapping[str, float]) -> tuple[float, str | None]:
    positive = {key: float(value) for key, value in values.items() if float(value) > 0.0}
    total = float(sum(positive.values()))
    if total <= 0.0:
        return 1.0, None
    key = max(positive, key=positive.get)
    return float(positive[key] / total), key


def _month_contributions(frame: pd.DataFrame) -> dict[str, float]:
    working = frame.copy()
    working["month"] = pd.to_datetime(working["start_utc"], utc=True).dt.strftime("%Y-%m")
    return {
        str(key): float(value)
        for key, value in working.groupby("month", sort=True)["net_return"].sum().items()
    }


def _universe_diagnostics(
    dynamic_universes: list[list[str]],
    fixed_symbols: list[str],
    entries: list[pd.Timestamp],
    dynamic_weights: np.ndarray,
    symbols: list[str],
    eligible_counts: list[int],
) -> dict[str, Any]:
    fixed_set = set(fixed_symbols)
    overlap = np.asarray([len(set(items) & fixed_set) for items in dynamic_universes], dtype=float)
    jaccard = np.asarray(
        [len(set(items) & fixed_set) / len(set(items) | fixed_set) for items in dynamic_universes],
        dtype=float,
    )
    universe_frequency: dict[str, int] = {}
    for items in dynamic_universes:
        for symbol in items:
            universe_frequency[symbol] = universe_frequency.get(symbol, 0) + 1
    selected_frequency = {
        symbol: int(np.count_nonzero(np.abs(dynamic_weights[:, column]) > 1e-15))
        for column, symbol in enumerate(symbols)
        if np.any(np.abs(dynamic_weights[:, column]) > 1e-15)
    }
    changes = []
    previous: set[str] | None = None
    for entry, items in zip(entries, dynamic_universes, strict=True):
        current = set(items)
        if previous is not None:
            entered = sorted(current - previous)
            exited = sorted(previous - current)
            if entered or exited:
                changes.append(
                    {
                        "entry_utc": entry,
                        "entered": entered,
                        "exited": exited,
                        "turnover_count": len(entered) + len(exited),
                    }
                )
        previous = current
    return {
        "mean_fixed_panel_overlap_count": float(overlap.mean()),
        "minimum_fixed_panel_overlap_count": int(overlap.min()),
        "maximum_fixed_panel_overlap_count": int(overlap.max()),
        "mean_fixed_panel_jaccard": float(jaccard.mean()),
        "eligible_count_min": int(min(eligible_counts)),
        "eligible_count_median": float(np.median(eligible_counts)),
        "eligible_count_max": int(max(eligible_counts)),
        "distinct_dynamic_universe_symbols": int(len(universe_frequency)),
        "top_universe_frequency": sorted(
            universe_frequency.items(), key=lambda item: (-item[1], item[0])
        )[:30],
        "top_selected_position_frequency": sorted(
            selected_frequency.items(), key=lambda item: (-item[1], item[0])
        )[:30],
        "universe_change_days": int(len(changes)),
        "largest_universe_changes": sorted(
            changes, key=lambda item: (-item["turnover_count"], str(item["entry_utc"]))
        )[:30],
    }


def _panel_result(
    protocol: dict[str, Any],
    name: str,
    entries: list[pd.Timestamp],
    weights: WeightBuildResult,
    symbols: list[str],
    open_prices: pd.DataFrame,
    close_prices: pd.DataFrame,
) -> dict[str, Any]:
    warmup = int(protocol["frozen_parent_signal"]["warmup_entries_excluded"])
    used_entries = entries[warmup:]
    used_weights = weights.weights[warmup:]
    costs = protocol["costs"]
    simulations: dict[str, SimulationResult] = {
        "base": _simulate(
            used_entries,
            used_weights,
            symbols,
            open_prices,
            close_prices,
            one_way_cost=float(costs["base_one_way_per_unit_turnover"]),
            funding_reserve_full_gross=float(costs["base_adverse_funding_reserve_per_24h_full_gross"]),
        ),
        "stress": _simulate(
            used_entries,
            used_weights,
            symbols,
            open_prices,
            close_prices,
            one_way_cost=float(costs["stress_one_way_per_unit_turnover"]),
            funding_reserve_full_gross=float(costs["stress_adverse_funding_reserve_per_24h_full_gross"]),
        ),
    }
    output: dict[str, Any] = {
        "name": name,
        "entries": len(used_entries),
        "periods": len(used_entries) - 1,
        "first_entry_utc": used_entries[0],
        "last_entry_utc": used_entries[-1],
        "cohort_forced_exits": weights.cohort_forced_exits,
        "forced_exit_events_at_decisions": int(weights.forced_exit_counts[warmup:].sum()),
        "refresh_decisions": int(weights.refresh_flags[warmup:].sum()),
    }
    for cost_name, simulation in simulations.items():
        metrics = _metrics(simulation.frame)
        segments = _segment_metrics(simulation.frame, protocol)
        symbol_share, symbol_key = _positive_share(simulation.symbol_contributions)
        months = _month_contributions(simulation.frame)
        month_share, month_key = _positive_share(months)
        output[cost_name] = {
            "metrics": metrics,
            "segments": segments,
            "positive_segment_count": _positive_segment_count(segments),
            "symbol_net_contributions": dict(
                sorted(
                    (
                        (symbol, value)
                        for symbol, value in simulation.symbol_contributions.items()
                        if abs(value) > 1e-15
                    ),
                    key=lambda item: (-item[1], item[0]),
                )
            ),
            "maximum_single_symbol_positive_net_contribution_share": symbol_share,
            "maximum_symbol": symbol_key,
            "month_net_contributions": months,
            "maximum_single_month_positive_net_contribution_share": month_share,
            "maximum_month": month_key,
            "terminal_exit_count": simulation.terminal_exit_count,
            "terminal_exit_symbols": simulation.terminal_exit_symbols,
        }
    return output


def _expand_panel(frame: pd.DataFrame, symbols: list[str]) -> pd.DataFrame:
    output = pd.DataFrame(index=frame.index, columns=symbols, dtype=float)
    for column in frame.columns:
        output[column] = frame[column].astype(float)
    return output


def _price_source_consistency(
    dynamic_open: pd.DataFrame,
    dynamic_close: pd.DataFrame,
    parent_open: pd.DataFrame,
    parent_close: pd.DataFrame,
    fixed_symbols: list[str],
) -> dict[str, Any]:
    output: dict[str, Any] = {"symbols": {}}
    overall_open_max = 0.0
    overall_close_max = 0.0
    total_compared = 0
    for symbol in fixed_symbols:
        common_index = (
            dynamic_open.index.intersection(parent_open.index)
            .intersection(dynamic_close.index)
            .intersection(parent_close.index)
        )
        joined = pd.DataFrame(
            {
                "dynamic_open": dynamic_open.loc[common_index, symbol],
                "parent_open": parent_open.loc[common_index, symbol],
                "dynamic_close": dynamic_close.loc[common_index, symbol],
                "parent_close": parent_close.loc[common_index, symbol],
            }
        ).dropna()
        if joined.empty:
            raise ValueError(f"no overlapping prices for fixed symbol {symbol}")
        open_relative = (
            (joined["dynamic_open"] - joined["parent_open"]).abs()
            / joined["parent_open"].abs().clip(lower=1e-12)
        )
        close_relative = (
            (joined["dynamic_close"] - joined["parent_close"]).abs()
            / joined["parent_close"].abs().clip(lower=1e-12)
        )
        symbol_open_max = float(open_relative.max())
        symbol_close_max = float(close_relative.max())
        overall_open_max = max(overall_open_max, symbol_open_max)
        overall_close_max = max(overall_close_max, symbol_close_max)
        total_compared += int(len(joined))
        output["symbols"][symbol] = {
            "overlapping_bars": int(len(joined)),
            "open_relative_difference_max": symbol_open_max,
            "open_relative_difference_median": float(open_relative.median()),
            "close_relative_difference_max": symbol_close_max,
            "close_relative_difference_median": float(close_relative.median()),
        }
    output["total_symbol_bars_compared"] = total_compared
    output["maximum_open_relative_difference"] = overall_open_max
    output["maximum_close_relative_difference"] = overall_close_max
    return output


def run() -> dict[str, Any]:
    protocol = _read_json(PROTOCOL_PATH)
    _validate_protocol(protocol)
    dataset_manifest, quality_report = _validate_inputs(protocol)
    open_prices, close_prices, quote_volume, symbols = _load_panels()
    fixed_symbols = [str(item) for item in protocol["comparison_panels"]["fixed_survivor_panel"]]
    parent_open_prices, parent_close_prices = _load_parent_fixed_panels(fixed_symbols)
    inputs = _build_signal_inputs(
        protocol,
        open_prices,
        close_prices,
        quote_volume,
        symbols,
        parent_open_prices,
        parent_close_prices,
    )
    entries = inputs["entries"]
    fixed_open_full = _expand_panel(parent_open_prices, symbols)
    fixed_close_full = _expand_panel(parent_close_prices, symbols)
    price_consistency = _price_source_consistency(
        open_prices,
        close_prices,
        parent_open_prices,
        parent_close_prices,
        fixed_symbols,
    )

    fixed_weights = _staggered_weights(protocol, inputs["fixed_mappings"], entries, symbols)
    dynamic_weights = _staggered_weights(protocol, inputs["dynamic_mappings"], entries, symbols)

    fixed = _panel_result(
        protocol,
        "fixed_survivor_panel",
        entries,
        fixed_weights,
        symbols,
        fixed_open_full,
        fixed_close_full,
    )
    dynamic = _panel_result(
        protocol, "dynamic_point_in_time_panel", entries, dynamic_weights, symbols, open_prices, close_prices
    )
    universe = _universe_diagnostics(
        inputs["dynamic_universes"],
        inputs["fixed_symbols"],
        entries,
        dynamic_weights.weights,
        symbols,
        inputs["eligible_counts"],
    )

    gates = protocol["fixed_gates"]
    base = dynamic["base"]
    stress = dynamic["stress"]
    base_metrics = base["metrics"]
    stress_metrics = stress["metrics"]
    checks = {
        "dynamic_base_profit_factor": float(base_metrics["profit_factor"] or 0.0)
        >= float(gates["dynamic_base_profit_factor_min"]),
        "dynamic_stress_profit_factor": float(stress_metrics["profit_factor"] or 0.0)
        >= float(gates["dynamic_stress_profit_factor_min"]),
        "dynamic_base_total_return_gt_zero": float(base_metrics["total_return"]) > 0.0,
        "dynamic_stress_total_return_gt_zero": float(stress_metrics["total_return"]) > 0.0,
        "dynamic_base_maximum_drawdown": abs(float(base_metrics["maximum_drawdown"]))
        <= float(gates["dynamic_base_maximum_drawdown_abs_max"]),
        "dynamic_stress_maximum_drawdown": abs(float(stress_metrics["maximum_drawdown"]))
        <= float(gates["dynamic_stress_maximum_drawdown_abs_max"]),
        "dynamic_positive_base_segments": int(base["positive_segment_count"])
        >= int(gates["dynamic_positive_base_segments_min"]),
        "dynamic_positive_stress_segments": int(stress["positive_segment_count"])
        >= int(gates["dynamic_positive_stress_segments_min"]),
        "maximum_single_symbol_positive_net_contribution_share": float(
            base["maximum_single_symbol_positive_net_contribution_share"]
        )
        <= float(gates["maximum_single_symbol_positive_net_contribution_share"]),
        "maximum_single_month_positive_net_contribution_share": float(
            base["maximum_single_month_positive_net_contribution_share"]
        )
        <= float(gates["maximum_single_month_positive_net_contribution_share"]),
        "minimum_mean_fixed_panel_overlap_count": float(universe["mean_fixed_panel_overlap_count"])
        >= float(gates["minimum_mean_fixed_panel_overlap_count"]),
        "maximum_fixed_symbol_overlapping_open_relative_difference": float(
            price_consistency["maximum_open_relative_difference"]
        )
        <= float(gates["maximum_fixed_symbol_overlapping_open_relative_difference"]),
        "maximum_fixed_symbol_overlapping_close_relative_difference": float(
            price_consistency["maximum_close_relative_difference"]
        )
        <= float(gates["maximum_fixed_symbol_overlapping_close_relative_difference"]),
    }
    all_pass = bool(all(checks.values()))
    positive_support = (
        float(base_metrics["total_return"]) > 0.0
        and float(stress_metrics["total_return"]) > 0.0
        and float(base_metrics["profit_factor"] or 0.0) >= 1.0
        and float(stress_metrics["profit_factor"] or 0.0) >= 1.0
    )
    decisions = protocol["decision_rules"]
    if all_pass:
        decision = decisions["all_gates_pass"]
    elif positive_support:
        decision = decisions["positive_but_gate_failure"]
    else:
        decision = decisions["nonpositive_or_profit_factor_below_one"]

    result = {
        "schema": "h22_dynamic_universe_survivorship_audit_result_v1",
        "protocol_id": protocol["protocol_id"],
        "protocol_sha256": _sha256(PROTOCOL_PATH),
        "script_sha256": _sha256(Path(__file__)),
        "dataset_manifest_sha256": _sha256(DATASET_MANIFEST_PATH),
        "parent_protocol_sha256": _sha256(PARENT_PROTOCOL_PATH),
        "status": "COMPLETE",
        "decision": decision,
        "all_fixed_gates_pass": all_pass,
        "fixed_gate_checks": checks,
        "dataset_summary": {
            "dataset_id": dataset_manifest["dataset_id"],
            "rows": dataset_manifest["storage"]["rows"],
            "unique_instruments": dataset_manifest["coverage"]["unique_instruments"],
            "quality_status": quality_report["status"],
        },
        "signal_timeline": {
            "raw_entries": len(entries),
            "warmup_entries_excluded": int(protocol["frozen_parent_signal"]["warmup_entries_excluded"]),
            "reported_entries": len(entries) - int(protocol["frozen_parent_signal"]["warmup_entries_excluded"]),
            "first_entry_utc": entries[int(protocol["frozen_parent_signal"]["warmup_entries_excluded"])],
            "last_entry_utc": entries[-1],
        },
        "universe_diagnostics": universe,
        "fixed_source_price_consistency": price_consistency,
        "fixed_survivor_panel": fixed,
        "dynamic_point_in_time_panel": dynamic,
        "interpretation_boundary": (
            "This audit changes only universe membership. It does not authorize parameter changes, symbol subset selection, production promotion or removal of the forward evidence requirement."
        ),
        "production_effect": "NONE",
        "formal_signal_effect": "NONE",
        "automatic_promotion": False,
    }
    _atomic_json(RESULT_PATH, result)
    _write_report(protocol, result)
    hashes = [
        f"{_sha256(PROTOCOL_PATH)}  {PROTOCOL_PATH.name}",
        f"{_sha256(Path(__file__))}  {Path(__file__).name}",
        f"{_sha256(RESULT_PATH)}  {RESULT_PATH.name}",
        f"{_sha256(REPORT_PATH)}  {REPORT_PATH.name}",
        f"{_sha256(DATASET_MANIFEST_PATH)}  DATASET_MANIFEST.json",
        f"{_sha256(PARENT_PROTOCOL_PATH)}  momentum_staggered_3x3_refresh_v1.json",
    ]
    _atomic_text(HASHES_PATH, "\n".join(hashes) + "\n")
    return result


def _format_metric(value: Any, digits: int = 4) -> str:
    if value is None:
        return "N/A"
    return f"{float(value):.{digits}f}"


def _write_report(protocol: dict[str, Any], result: dict[str, Any]) -> None:
    fixed = result["fixed_survivor_panel"]
    dynamic = result["dynamic_point_in_time_panel"]
    universe = result["universe_diagnostics"]
    checks = result["fixed_gate_checks"]

    def panel_rows(panel: dict[str, Any]) -> str:
        base = panel["base"]["metrics"]
        stress = panel["stress"]["metrics"]
        return (
            f"| 基础成本 | {_format_metric(base.get('profit_factor'))} | {_format_metric(base.get('win_rate'))} | "
            f"{_format_metric(base.get('payoff_ratio'))} | {_format_metric(base.get('total_return'))} | "
            f"{_format_metric(base.get('maximum_drawdown'))} | {_format_metric(base.get('mean_turnover'))} |\n"
            f"| 压力成本 | {_format_metric(stress.get('profit_factor'))} | {_format_metric(stress.get('win_rate'))} | "
            f"{_format_metric(stress.get('payoff_ratio'))} | {_format_metric(stress.get('total_return'))} | "
            f"{_format_metric(stress.get('maximum_drawdown'))} | {_format_metric(stress.get('mean_turnover'))} |"
        )

    failed_checks = [name for name, passed in checks.items() if not passed]
    report = f"""# H22动态交易宇宙存活者偏差审计

状态：`{result['status']}`

决定：`{result['decision']}`

协议：`{result['protocol_id']}`

## 一、研究边界

本轮只替换H22的币种名单，不修改14日形成期、4入6出、三组错开、3日刷新、04:00 UTC入场、0.4总敞口和成本门槛。动态名单在每个信号时点只使用当时已经闭合的数据：连续85根4小时K线，并按过去84根成交额选择前18名。

## 二、数据与宇宙

- 数据集质量：`{result['dataset_summary']['quality_status']}`；
- 历史标的总数：{result['dataset_summary']['unique_instruments']}；
- 动态宇宙实际使用过的标的：{universe['distinct_dynamic_universe_symbols']}；
- 每个时点进入成交额排名前的合格标的：最少 {universe['eligible_count_min']}，中位数 {universe['eligible_count_median']:.1f}，最多 {universe['eligible_count_max']}；
- 动态18币与固定18币平均重合：{universe['mean_fixed_panel_overlap_count']:.2f} 个；
- 最低重合：{universe['minimum_fixed_panel_overlap_count']} 个，最高重合：{universe['maximum_fixed_panel_overlap_count']} 个；
- 平均Jaccard重合度：{universe['mean_fixed_panel_jaccard']:.4f}；
- 动态名单发生变化的信号日：{universe['universe_change_days']}。

## 三、固定18币同源基准

| 成本 | PF | 胜率 | 盈亏比 | 总收益 | 最大回撤 | 平均换手 |
|---|---:|---:|---:|---:|---:|---:|
{panel_rows(fixed)}

## 四、动态点时18币结果

| 成本 | PF | 胜率 | 盈亏比 | 总收益 | 最大回撤 | 平均换手 |
|---|---:|---:|---:|---:|---:|---:|
{panel_rows(dynamic)}

动态基础成本正收益分段：{dynamic['base']['positive_segment_count']} / 3。

动态压力成本正收益分段：{dynamic['stress']['positive_segment_count']} / 3。

最大单币正贡献占比：{dynamic['base']['maximum_single_symbol_positive_net_contribution_share']:.4f}（{dynamic['base']['maximum_symbol']}）。

最大单月正贡献占比：{dynamic['base']['maximum_single_month_positive_net_contribution_share']:.4f}（{dynamic['base']['maximum_month']}）。

决策时点强制退出次数：{dynamic['forced_exit_events_at_decisions']}。

持有期内退市终止退出次数：{dynamic['base']['terminal_exit_count']}。

## 五、固定门禁

"""
    for name, passed in checks.items():
        report += f"- {'通过' if passed else '失败'}：`{name}`\n"
    report += f"""

失败门禁数量：{len(failed_checks)}。

失败门禁：{', '.join(failed_checks) if failed_checks else '无'}。

## 六、结论约束

`{result['decision']}`

通过也只代表H22的历史证据不完全依赖今天仍存活的固定币种名单，仍必须继续前向影子验收；失败则不得通过改成其他成交额窗口、其他动态币种数量、删除退市币或挑选月份来补救。本轮不会改变正式信号、A级状态、杠杆或下单边界。
"""
    _atomic_text(REPORT_PATH, report)


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2, default=_json_default, allow_nan=False))
