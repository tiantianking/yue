from __future__ import annotations

"""Frozen evaluation for a sector-capped version of the existing momentum shadow.

This runner does not search for an independent Alpha family. It evaluates one
predeclared portfolio-construction change to the 14-day cross-sectional
momentum research shadow, includes archived OKX funding cashflows, and writes
all falsification and portfolio-increment evidence before making a terminal
research-only decision.
"""

import argparse
import hashlib
import json
import math
import shutil
import sys
import zipfile
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
WORKSPACE_ROOT = PROJECT_ROOT.parents[1]
DISCOVERY_DIR = (
    WORKSPACE_ROOT
    / "HISTORY_PACKAGES_20260621"
    / "RESEARCH"
    / "local_only_hypothesis_discovery_v1"
)
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))
if str(DISCOVERY_DIR) not in sys.path:
    sys.path.insert(0, str(DISCOVERY_DIR))

import momentum_overlay_common as common
from okx_signal_system.research.sector_balanced_momentum import (
    maximum_loss_streak,
    maximum_sector_slot_share,
    maximum_symbol_slot_share,
    sector_capped_hysteresis_weights,
    sector_capped_rank_weights,
)

PROTOCOL_PATH = PROJECT_ROOT / "config" / "research_protocols" / "momentum_sector_balance_v1.json"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "outputs" / "research" / "momentum_sector_balance_v1"
FUNDING_ARCHIVE = WORKSPACE_ROOT / "历史数据_保留" / "imports" / "okx_funding_rate_history" / "monthly"
FUNDING_SNAPSHOT = (
    WORKSPACE_ROOT
    / "HISTORY_PACKAGES_20260621"
    / "RESEARCH"
    / "basis_anchor_r1"
    / "data_snapshot_20260621"
    / "funding"
)
OPENED_HISTORY_CUTOFF = pd.Timestamp("2026-06-16T12:00:00Z")
START = pd.Timestamp("2023-09-01T04:00:00Z")
SEGMENTS = [
    ("S1", pd.Timestamp("2023-09-01T04:00:00Z"), pd.Timestamp("2024-07-01T00:00:00Z")),
    ("S2", pd.Timestamp("2024-07-01T00:00:00Z"), pd.Timestamp("2025-07-01T00:00:00Z")),
    ("S3", pd.Timestamp("2025-07-01T00:00:00Z"), OPENED_HISTORY_CUTOFF + pd.Timedelta(days=1)),
]


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON object required: {path}")
    return payload


def _json_default(value: Any) -> Any:
    if isinstance(value, (pd.Timestamp, np.datetime64)):
        return pd.Timestamp(value).isoformat()
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        return float(value)
    if isinstance(value, np.ndarray):
        return value.tolist()
    raise TypeError(f"unsupported JSON value: {type(value)!r}")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default) + "\n",
        encoding="utf-8",
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _protocol_inputs(protocol: dict[str, Any]) -> tuple[list[str], dict[str, str]]:
    if protocol.get("status") != "LOCKED_BEFORE_PNL":
        raise ValueError("protocol must be locked before outcomes are opened")
    universe = protocol.get("universe", {})
    symbols = [str(value) for value in universe.get("symbols", [])]
    if len(symbols) != 18 or len(set(symbols)) != 18:
        raise ValueError("frozen universe must contain 18 unique mature symbols")
    sector_by_symbol: dict[str, str] = {}
    for sector, members in universe.get("sectors", {}).items():
        for symbol in members:
            if symbol in sector_by_symbol:
                raise ValueError(f"duplicate sector membership: {symbol}")
            sector_by_symbol[str(symbol)] = str(sector)
    if set(sector_by_symbol) != set(symbols):
        raise ValueError("sector taxonomy must cover the frozen universe exactly")
    return symbols, sector_by_symbol


def _read_monthly_funding(symbol: str) -> pd.DataFrame:
    rows: list[pd.DataFrame] = []
    symbol_dir = FUNDING_ARCHIVE / symbol
    for archive in sorted(symbol_dir.glob("*.zip")):
        with zipfile.ZipFile(archive) as handle:
            members = [name for name in handle.namelist() if name.lower().endswith(".csv")]
            if len(members) != 1:
                raise ValueError(f"{archive}: expected one CSV")
            with handle.open(members[0]) as stream:
                frame = pd.read_csv(stream)
        required = {"instrument_name", "funding_rate", "funding_time"}
        if not required.issubset(frame.columns):
            raise ValueError(f"{archive}: missing funding columns")
        frame["funding_time"] = pd.to_datetime(frame["funding_time"], unit="ms", utc=True)
        rows.append(frame[["funding_time", "funding_rate"]])

    snapshot = FUNDING_SNAPSHOT / f"{symbol.replace('-', '_')}_funding.parquet"
    if snapshot.is_file():
        frame = pd.read_parquet(snapshot, columns=["funding_time", "funding_rate"])
        frame["funding_time"] = pd.to_datetime(frame["funding_time"], utc=True)
        rows.append(frame[["funding_time", "funding_rate"]])
    if not rows:
        raise FileNotFoundError(f"funding history missing: {symbol}")

    result = pd.concat(rows, ignore_index=True)
    result["funding_rate"] = pd.to_numeric(result["funding_rate"], errors="coerce")
    result = result.dropna(subset=["funding_time", "funding_rate"])
    result = result.sort_values("funding_time").drop_duplicates("funding_time", keep="last")
    return result.loc[result["funding_time"] <= OPENED_HISTORY_CUTOFF].reset_index(drop=True)


def _load_funding(symbols: list[str]) -> tuple[dict[str, pd.DataFrame], dict[str, Any]]:
    funding = {symbol: _read_monthly_funding(symbol) for symbol in symbols}
    rows = []
    for symbol in symbols:
        frame = funding[symbol]
        rows.append(
            {
                "symbol": symbol,
                "events": int(len(frame)),
                "first": frame["funding_time"].min().isoformat() if not frame.empty else None,
                "last": frame["funding_time"].max().isoformat() if not frame.empty else None,
            }
        )
    coverage = {
        "symbols": len(rows),
        "all_nonempty": all(row["events"] > 0 for row in rows),
        "minimum_events": min(row["events"] for row in rows),
        "rows": rows,
    }
    if not coverage["all_nonempty"]:
        raise ValueError("funding coverage is incomplete")
    return funding, coverage


def _funding_rate_matrix(
    entries: list[pd.Timestamp],
    symbols: list[str],
    funding: dict[str, pd.DataFrame],
) -> np.ndarray:
    matrix = np.zeros((len(entries) - 1, len(symbols)), dtype=float)
    for column, symbol in enumerate(symbols):
        frame = funding[symbol]
        times = frame["funding_time"].to_numpy(dtype="datetime64[ns]")
        values = frame["funding_rate"].to_numpy(dtype=float)
        cumulative = np.concatenate([[0.0], np.cumsum(values)])
        for row, (start, end) in enumerate(zip(entries[:-1], entries[1:])):
            left = int(np.searchsorted(times, start.to_datetime64(), side="right"))
            right = int(np.searchsorted(times, end.to_datetime64(), side="right"))
            matrix[row, column] = cumulative[right] - cumulative[left]
    return matrix


def _simulate(
    entries: list[pd.Timestamp],
    targets: np.ndarray,
    open_prices: pd.DataFrame,
    funding_rates: np.ndarray,
    *,
    one_way_cost: float,
    adverse_funding_multiplier: float,
) -> pd.DataFrame:
    if len(entries) < 2:
        return pd.DataFrame()
    if targets.shape[0] != len(entries):
        raise ValueError("one target row is required per entry")
    if funding_rates.shape != (len(entries) - 1, targets.shape[1]):
        raise ValueError("funding matrix shape mismatch")

    previous_target = np.zeros(targets.shape[1], dtype=float)
    pending_turnover = float(np.abs(targets[0] - previous_target).sum())
    rows: list[dict[str, Any]] = []
    for index, (start, end) in enumerate(zip(entries[:-1], entries[1:])):
        start_open = open_prices.loc[start].to_numpy(dtype=float)
        end_open = open_prices.loc[end].to_numpy(dtype=float)
        asset_return = end_open / start_open - 1.0
        weights = targets[index]
        gross = float(np.dot(weights, asset_return))
        transaction_cost = pending_turnover * one_way_cost
        funding_components = -weights * funding_rates[index]
        stressed_components = np.where(
            funding_components < 0.0,
            funding_components * adverse_funding_multiplier,
            funding_components,
        )
        funding_return = float(stressed_components.sum())
        net = gross - transaction_cost + funding_return
        equity_factor = 1.0 + gross
        drifted = weights if equity_factor <= 0.0 else weights * (1.0 + asset_return) / equity_factor
        next_target = targets[index + 1]
        next_turnover = float(np.abs(next_target - drifted).sum())
        rows.append(
            {
                "start_utc": start,
                "end_utc": end,
                "gross_return": gross,
                "transaction_cost": transaction_cost,
                "funding_return": funding_return,
                "net_return": net,
                "turnover": pending_turnover,
                "gross_exposure": float(np.abs(weights).sum()),
            }
        )
        pending_turnover = next_turnover

    if rows:
        close_turnover = float(np.abs(targets[len(rows) - 1]).sum())
        close_cost = close_turnover * one_way_cost
        rows[-1]["transaction_cost"] += close_cost
        rows[-1]["net_return"] -= close_cost
        rows[-1]["turnover"] += close_turnover
    return pd.DataFrame(rows)


def _profit_factor(values: pd.Series) -> float | None:
    positive = float(values[values > 0.0].sum())
    negative = float(-values[values < 0.0].sum())
    if negative == 0.0:
        return None if positive == 0.0 else float("inf")
    return positive / negative


def _metrics(frame: pd.DataFrame) -> dict[str, float | int | None]:
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
        "maximum_loss_streak": maximum_loss_streak(values),
        "mean_turnover": float(frame["turnover"].mean()),
        "transaction_cost": float(frame["transaction_cost"].sum()),
        "funding_return": float(frame["funding_return"].sum()),
        "mean_gross_exposure": float(frame["gross_exposure"].mean()),
    }


def _segment_metrics(frame: pd.DataFrame) -> dict[str, dict[str, float | int | None]]:
    output: dict[str, dict[str, float | int | None]] = {}
    for name, start, end in SEGMENTS:
        subset = frame.loc[(frame["start_utc"] >= start) & (frame["start_utc"] < end)]
        output[name] = _metrics(subset)
    return output


def _plain_weight_matrix(
    mappings: list[dict[str, float]],
    symbols: list[str],
    sector_by_symbol: dict[str, str],
    *,
    cap: int,
) -> np.ndarray:
    return np.asarray(
        [
            sector_capped_rank_weights(
                pd.Series(mapping, dtype=float),
                symbols,
                sector_by_symbol,
                top_n=4,
                max_per_sector=cap,
            )[0]
            for mapping in mappings
        ],
        dtype=float,
    )


def _build_signal_inputs(
    panels: common.MarketPanels,
    symbols: list[str],
) -> tuple[list[pd.Timestamp], list[dict[str, float]], pd.DataFrame, pd.DataFrame]:
    close = panels.h4_close.loc[:, symbols]
    simple_score = close / close.shift(84) - 1.0
    log_score = np.log(close) - np.log(close.shift(84))
    entries: list[pd.Timestamp] = []
    mappings: list[dict[str, float]] = []
    signal_times = simple_score.index[simple_score.index.hour == 0]
    for signal_time in signal_times:
        entry = pd.Timestamp(signal_time) + pd.Timedelta(hours=4)
        if entry < START or entry > OPENED_HISTORY_CUTOFF:
            continue
        if entry not in panels.h4_open.index:
            continue
        current = simple_score.loc[signal_time]
        if current.isna().any():
            continue
        entries.append(entry)
        mappings.append({symbol: float(current[symbol]) for symbol in symbols})
    return entries, mappings, simple_score, log_score


def _representation_agreement(
    entries: list[pd.Timestamp],
    simple_score: pd.DataFrame,
    log_score: pd.DataFrame,
    symbols: list[str],
    sector_by_symbol: dict[str, str],
) -> float:
    agreements = []
    for entry in entries:
        signal_time = entry - pd.Timedelta(hours=4)
        simple_weights = sector_capped_rank_weights(
            simple_score.loc[signal_time], symbols, sector_by_symbol, top_n=4, max_per_sector=2
        )[0]
        log_weights = sector_capped_rank_weights(
            log_score.loc[signal_time], symbols, sector_by_symbol, top_n=4, max_per_sector=2
        )[0]
        agreements.append(bool(np.array_equal(simple_weights, log_weights)))
    return float(np.mean(agreements))


def _regime_labels(
    panels: common.MarketPanels,
    entries: list[pd.Timestamp],
    symbols: list[str],
) -> dict[pd.Timestamp, str]:
    market_return = np.log(panels.h1_close.loc[:, symbols]).diff().mean(axis=1)
    trend = market_return.rolling(28 * 24, min_periods=28 * 24).sum()
    volatility = market_return.rolling(7 * 24, min_periods=7 * 24).std(ddof=0)
    volatility_anchor = volatility.rolling(180 * 24, min_periods=90 * 24).median().shift(1)
    labels: dict[pd.Timestamp, str] = {}
    for entry in entries[:-1]:
        last_closed_hour = entry - pd.Timedelta(hours=1)
        if last_closed_hour not in market_return.index:
            continue
        current_trend = trend.loc[last_closed_hour]
        current_vol = volatility.loc[last_closed_hour]
        anchor = volatility_anchor.loc[last_closed_hour]
        if pd.isna(current_trend) or pd.isna(current_vol) or pd.isna(anchor):
            continue
        labels[entry] = f"{'UP' if current_trend >= 0.0 else 'DOWN'}_{'HIGH_VOL' if current_vol >= anchor else 'LOW_VOL'}"
    return labels


def _positive_regime_count(frame: pd.DataFrame, labels: dict[pd.Timestamp, str]) -> int:
    if frame.empty:
        return 0
    mapped = frame.copy()
    mapped["regime"] = mapped["start_utc"].map(labels)
    mapped = mapped.dropna(subset=["regime"])
    if mapped.empty:
        return 0
    means = mapped.groupby("regime", observed=True)["net_return"].mean()
    return int((means > 0.0).sum())


def _effective_signal_count(weights: np.ndarray) -> int:
    if len(weights) == 0:
        return 0
    changes = np.any(np.abs(np.diff(weights, axis=0)) > 1e-12, axis=1)
    return 1 + int(np.count_nonzero(changes))


def _falsification_rows(
    entries: list[pd.Timestamp],
    primary: np.ndarray,
    h4_open: pd.DataFrame,
    delayed_open: pd.DataFrame,
    funding_rates: np.ndarray,
    delayed_funding_rates: np.ndarray,
    protocol: dict[str, Any],
) -> tuple[pd.DataFrame, dict[str, Any], pd.DataFrame, pd.DataFrame]:
    costs = protocol["costs"]
    frozen = protocol["falsification"]
    observed_frame = _simulate(
        entries,
        primary,
        h4_open,
        funding_rates,
        one_way_cost=float(costs["one_way_baseline"]),
        adverse_funding_multiplier=1.0,
    )
    reverse_frame = _simulate(
        entries,
        -primary,
        h4_open,
        funding_rates,
        one_way_cost=float(costs["one_way_baseline"]),
        adverse_funding_multiplier=1.0,
    )
    delayed_entries = [entry + pd.Timedelta(minutes=int(frozen["entry_delay_minutes"])) for entry in entries]
    delayed_frame = _simulate(
        delayed_entries,
        primary,
        delayed_open,
        delayed_funding_rates,
        one_way_cost=float(costs["one_way_baseline"]),
        adverse_funding_multiplier=1.0,
    )

    observed = _metrics(observed_frame)
    reversed_metrics = _metrics(reverse_frame)
    delayed = _metrics(delayed_frame)
    rows = [
        {
            "test": "observed",
            "trial_id": 0,
            "net_r": observed["net_r"],
            "profit_factor": observed["profit_factor"],
            "total_trades": observed["periods"],
        },
        {
            "test": "direction_reversed",
            "trial_id": 0,
            "net_r": reversed_metrics["net_r"],
            "profit_factor": reversed_metrics["profit_factor"],
            "total_trades": reversed_metrics["periods"],
        },
        {
            "test": "entry_delay_1bar",
            "trial_id": 0,
            "net_r": delayed["net_r"],
            "profit_factor": delayed["profit_factor"],
            "total_trades": delayed["periods"],
        },
    ]

    trials = int(frozen["random_time_trials"])
    rng = np.random.default_rng(int(frozen["random_seed"]))
    offsets = np.arange(1, len(primary) - 1, dtype=int)
    selected_offsets = rng.choice(offsets, size=trials, replace=len(offsets) < trials)
    random_net: list[float] = []
    for trial_id, offset in enumerate(selected_offsets, start=1):
        shifted = np.roll(primary, int(offset), axis=0)
        trial_frame = _simulate(
            entries,
            shifted,
            h4_open,
            funding_rates,
            one_way_cost=float(costs["one_way_baseline"]),
            adverse_funding_multiplier=1.0,
        )
        trial_metrics = _metrics(trial_frame)
        random_net.append(float(trial_metrics["net_r"] or 0.0))
        rows.append(
            {
                "test": "random_time",
                "trial_id": trial_id,
                "net_r": trial_metrics["net_r"],
                "profit_factor": trial_metrics["profit_factor"],
                "total_trades": trial_metrics["periods"],
            }
        )

    random_values = np.asarray(random_net, dtype=float)
    observed_net = float(observed["net_r"] or 0.0)
    random_q95 = float(np.quantile(random_values, 0.95))
    empirical_p = float((1 + np.count_nonzero(random_values >= observed_net)) / (1 + len(random_values)))
    reverse_pf_gap = float(observed["profit_factor"] or 0.0) - float(reversed_metrics["profit_factor"] or 0.0)
    reverse_fraction = (
        float(reversed_metrics["net_r"] or 0.0) / observed_net if observed_net > 0.0 else float("inf")
    )
    delayed_retention = float(delayed["net_r"] or 0.0) / observed_net if observed_net > 0.0 else None
    gates = protocol["robustness_gates"]
    checks = {
        "observed_above_random_95th_percentile": observed_net > random_q95,
        "random_time_empirical_p_not_above_alpha": empirical_p <= float(gates["maximum_random_time_empirical_p"]),
        "reverse_profit_factor_gap_at_least_minimum": reverse_pf_gap >= float(gates["minimum_reverse_pf_gap"]),
        "reverse_net_r_fraction_not_above_maximum": reverse_fraction <= float(gates["maximum_reverse_net_r_fraction"]),
        "delayed_profit_factor_at_least_one": float(delayed["profit_factor"] or 0.0) >= float(gates["minimum_delayed_profit_factor"]),
        "delayed_net_r_positive": float(delayed["net_r"] or 0.0) > 0.0,
        "delayed_net_r_retention_at_least_minimum": delayed_retention is not None
        and delayed_retention >= float(gates["minimum_delayed_net_r_retention"]),
    }
    summary = {
        "observed": observed,
        "direction_reversed": reversed_metrics,
        "entry_delay_15m": delayed,
        "random_time": {
            "trials": trials,
            "q95_net_r": random_q95,
            "empirical_p": empirical_p,
            "mean_net_r": float(random_values.mean()),
            "median_net_r": float(np.median(random_values)),
        },
        "comparison": {
            "reverse_profit_factor_gap": reverse_pf_gap,
            "reverse_net_r_fraction": reverse_fraction,
            "delayed_net_r_retention": delayed_retention,
        },
        "checks": checks,
        "passed": bool(all(checks.values())),
    }
    return pd.DataFrame(rows), summary, observed_frame, delayed_frame


def _parameter_neighborhood(
    entries: list[pd.Timestamp],
    variants: dict[str, np.ndarray],
    h4_open: pd.DataFrame,
    funding_rates: np.ndarray,
    protocol: dict[str, Any],
) -> tuple[pd.DataFrame, dict[str, Any]]:
    declarations = {item["config_id"]: item for item in protocol["parameter_neighborhood"]}
    rows = []
    for config_id, weights in variants.items():
        frame = _simulate(
            entries,
            weights,
            h4_open,
            funding_rates,
            one_way_cost=float(protocol["costs"]["one_way_baseline"]),
            adverse_funding_multiplier=1.0,
        )
        metrics = _metrics(frame)
        declaration = declarations[config_id]
        rows.append(
            {
                "config_id": config_id,
                "is_primary": bool(declaration["is_primary"]),
                "distance": float(declaration["distance"]),
                "net_r": metrics["net_r"],
                "profit_factor": metrics["profit_factor"],
                "total_trades": metrics["periods"],
            }
        )
    frame = pd.DataFrame(rows)
    neighbors = frame.loc[~frame["is_primary"]]
    primary_pf = float(frame.loc[frame["is_primary"], "profit_factor"].iloc[0])
    positive_ratio = float((neighbors["net_r"] > 0.0).mean())
    median_pf = float(neighbors["profit_factor"].median())
    gates = protocol["robustness_gates"]
    checks = {
        "at_least_three_neighbors": len(neighbors) >= 3,
        "positive_neighbor_ratio_at_least_minimum": positive_ratio >= float(gates["minimum_positive_neighbor_ratio"]),
        "neighbor_median_profit_factor_at_least_one": median_pf >= float(gates["minimum_neighbor_median_profit_factor"]),
        "primary_pf_not_above_twice_neighbor_median": primary_pf
        <= float(gates["maximum_primary_to_neighbor_median_pf_ratio"]) * median_pf,
    }
    return frame, {
        "positive_neighbor_ratio": positive_ratio,
        "neighbor_median_profit_factor": median_pf,
        "primary_profit_factor": primary_pf,
        "checks": checks,
        "passed": bool(all(checks.values())),
    }


def _portfolio_increment(
    entries: list[pd.Timestamp],
    baseline: np.ndarray,
    primary: np.ndarray,
    h4_open: pd.DataFrame,
    funding_rates: np.ndarray,
    regime_labels: dict[pd.Timestamp, str],
    protocol: dict[str, Any],
) -> tuple[pd.DataFrame, dict[str, Any]]:
    combined = 0.5 * baseline + 0.5 * primary
    scenarios = {"baseline": baseline, "combined": combined}
    rows = []
    metrics_by_name: dict[str, dict[str, Any]] = {}
    for name, weights in scenarios.items():
        frame = _simulate(
            entries,
            weights,
            h4_open,
            funding_rates,
            one_way_cost=float(protocol["costs"]["one_way_baseline"]),
            adverse_funding_multiplier=1.0,
        )
        metrics = _metrics(frame)
        metrics["effective_signal_count"] = _effective_signal_count(weights)
        metrics["regime_coverage_count"] = _positive_regime_count(frame, regime_labels)
        metrics_by_name[name] = metrics
        rows.append(
            {
                "scenario": name,
                "profit_factor": metrics["profit_factor"],
                "max_drawdown": metrics["maximum_drawdown"],
                "max_loss_streak": metrics["maximum_loss_streak"],
                "effective_signal_count": metrics["effective_signal_count"],
                "regime_coverage_count": metrics["regime_coverage_count"],
            }
        )

    base = metrics_by_name["baseline"]
    combo = metrics_by_name["combined"]
    gates = protocol["portfolio_increment_gates"]
    required = gates["required_improvements_any"]
    no_deterioration = {
        "pf_deterioration_within_limit": float(combo["profit_factor"] or 0.0)
        >= float(base["profit_factor"] or 0.0) - float(gates["maximum_pf_deterioration"]),
        "drawdown_deterioration_within_limit": abs(float(combo["maximum_drawdown"] or 0.0))
        <= abs(float(base["maximum_drawdown"] or 0.0)) + float(gates["maximum_drawdown_deterioration"]),
        "loss_streak_increase_within_limit": int(combo["maximum_loss_streak"] or 0)
        <= int(base["maximum_loss_streak"] or 0) + int(gates["maximum_loss_streak_increase"]),
    }
    minimum_effective = max(
        int(base["effective_signal_count"] * (1.0 + float(required["effective_signal_fraction"]))),
        int(base["effective_signal_count"] + int(required["effective_signal_absolute"])),
    )
    improvements = {
        "profit_factor_improved": float(combo["profit_factor"] or 0.0)
        >= float(base["profit_factor"] or 0.0) + float(required["profit_factor"]),
        "maximum_drawdown_improved": abs(float(combo["maximum_drawdown"] or 0.0))
        <= abs(float(base["maximum_drawdown"] or 0.0)) - float(required["maximum_drawdown"]),
        "maximum_loss_streak_improved": int(combo["maximum_loss_streak"] or 0)
        <= int(base["maximum_loss_streak"] or 0) - int(required["maximum_loss_streak"]),
        "effective_signal_count_improved": int(combo["effective_signal_count"]) >= minimum_effective,
        "positive_regime_count_improved": int(combo["regime_coverage_count"])
        >= int(base["regime_coverage_count"]) + int(required["positive_regime_count"]),
    }
    passed = bool(all(no_deterioration.values()) and any(improvements.values()))
    return pd.DataFrame(rows), {
        "metrics": metrics_by_name,
        "no_deterioration_checks": no_deterioration,
        "improvement_checks": improvements,
        "passed": passed,
    }


def _markdown(result: dict[str, Any]) -> str:
    primary = result["performance"]["primary_base"]
    stress = result["performance"]["primary_stress"]
    baseline = result["performance"]["baseline_base"]
    decision = result["decision"]
    failed_groups = [name for name, value in result["gate_groups"].items() if not value["passed"]]
    lines = [
        "# 14日动量板块限额组合 V1：研究结论",
        "",
        f"最终状态：`{decision}`",
        "",
        "本轮只检验现有14日横截面动量的组合构建改进，不登记独立Alpha，不分配新H编号，不修改正式信号或下单路径。",
        "",
        "## 冻结规则",
        "",
        "- 18个具备完整OKX资金费历史的成熟USDT永续；",
        "- 14日闭合4小时收益排名；",
        "- 4入6出迟滞；",
        "- 每侧同一固定板块最多2个币；",
        "- 每日04:00 UTC下一可交易开盘执行，持有24小时；",
        "- 计入实际OKX资金费、16bp基础往返成本与32bp压力往返成本；",
        "- 预先冻结500次随机时点、方向反转、15分钟延迟、三个参数邻域和组合增量门禁。",
        "",
        "## 核心结果",
        "",
        f"- 基准4入6出：PF {float(baseline['profit_factor'] or 0.0):.4f}，总收益 {float(baseline['total_return'] or 0.0):.2%}，最大回撤 {float(baseline['maximum_drawdown'] or 0.0):.2%}；",
        f"- 板块限额主版本：PF {float(primary['profit_factor'] or 0.0):.4f}，总收益 {float(primary['total_return'] or 0.0):.2%}，最大回撤 {float(primary['maximum_drawdown'] or 0.0):.2%}；",
        f"- 主版本压力成本：PF {float(stress['profit_factor'] or 0.0):.4f}，总收益 {float(stress['total_return'] or 0.0):.2%}；",
        f"- 平均换手：{float(primary['mean_turnover'] or 0.0):.4f}；资金费净贡献：{float(primary['funding_return'] or 0.0):.4%}。",
        "",
        "## 门禁",
        "",
    ]
    for name, value in result["gate_groups"].items():
        lines.append(f"- {name}: {'通过' if value['passed'] else '失败'}")
    lines.extend(
        [
            "",
            "## 决策",
            "",
            (
                "全部冻结门禁通过，只允许登记为独立研究影子并继续前向观察；仍不得视为A级。"
                if not failed_groups
                else "失败门禁：" + "、".join(failed_groups) + "。按冻结协议永久归档，禁止修改板块、上限、窗口、方向、币种、成本或持有期营救。"
            ),
            "",
            "生产系统影响：`NONE`  ",
            "自动下单影响：`NONE`",
            "",
        ]
    )
    return "\n".join(lines)


def _archive_failure(output_dir: Path, result: dict[str, Any]) -> Path:
    desktop = Path.home() / "Desktop" / "失败策略"
    archive_dir = desktop / str(result["protocol_id"])
    archive_dir.mkdir(parents=True, exist_ok=True)
    for path in output_dir.iterdir():
        if path.is_file():
            shutil.copy2(path, archive_dir / path.name)
    shutil.copy2(PROTOCOL_PATH, archive_dir / PROTOCOL_PATH.name)
    failure_summary = {
        "candidate_id": result["protocol_id"],
        "status": result["decision"],
        "failed_stage": "historical_robustness_and_incremental_value",
        "pnl_opened": True,
        "independent_alpha_claim": False,
        "failed_gate_groups": [name for name, item in result["gate_groups"].items() if not item["passed"]],
        "no_rescue": True,
        "production_effect": "NONE",
    }
    _write_json(archive_dir / "failure_summary.json", failure_summary)
    (archive_dir / "失败说明.md").write_text(_markdown(result), encoding="utf-8")
    return archive_dir


def run(output_dir: Path) -> dict[str, Any]:
    protocol = _read_json(PROTOCOL_PATH)
    symbols, sector_by_symbol = _protocol_inputs(protocol)
    panels = common.load_panels()
    entries, mappings, simple_score, log_score = _build_signal_inputs(panels, symbols)
    if len(entries) < 500:
        raise ValueError(f"insufficient daily signals: {len(entries)}")

    primary = sector_capped_hysteresis_weights(
        mappings,
        symbols,
        sector_by_symbol,
        top_n=4,
        exit_rank=6,
        max_per_sector=2,
    )
    baseline = sector_capped_hysteresis_weights(
        mappings,
        symbols,
        sector_by_symbol,
        top_n=4,
        exit_rank=6,
        max_per_sector=4,
    )
    variants = {
        "primary_cap2_hysteresis6": primary,
        "neighbor_cap3_hysteresis6": sector_capped_hysteresis_weights(
            mappings, symbols, sector_by_symbol, top_n=4, exit_rank=6, max_per_sector=3
        ),
        "neighbor_cap2_plain": _plain_weight_matrix(mappings, symbols, sector_by_symbol, cap=2),
        "neighbor_cap3_plain": _plain_weight_matrix(mappings, symbols, sector_by_symbol, cap=3),
    }

    h4_open = panels.h4_open.loc[:, symbols]
    delayed_entries = [entry + pd.Timedelta(minutes=15) for entry in entries]
    if any(entry not in panels.m15_open.index for entry in delayed_entries):
        missing = [entry for entry in delayed_entries if entry not in panels.m15_open.index]
        raise ValueError(f"missing delayed execution bars: {missing[:3]}")
    delayed_open = panels.m15_open.loc[:, symbols]

    funding, funding_coverage = _load_funding(symbols)
    funding_rates = _funding_rate_matrix(entries, symbols, funding)
    delayed_funding_rates = _funding_rate_matrix(delayed_entries, symbols, funding)

    base_cost = float(protocol["costs"]["one_way_baseline"])
    stress_cost = float(protocol["costs"]["one_way_stress"])
    baseline_frame = _simulate(
        entries,
        baseline,
        h4_open,
        funding_rates,
        one_way_cost=base_cost,
        adverse_funding_multiplier=1.0,
    )
    primary_base_frame = _simulate(
        entries,
        primary,
        h4_open,
        funding_rates,
        one_way_cost=base_cost,
        adverse_funding_multiplier=1.0,
    )
    primary_stress_frame = _simulate(
        entries,
        primary,
        h4_open,
        funding_rates,
        one_way_cost=stress_cost,
        adverse_funding_multiplier=2.0,
    )
    baseline_metrics = _metrics(baseline_frame)
    primary_base = _metrics(primary_base_frame)
    primary_stress = _metrics(primary_stress_frame)
    segment_metrics = _segment_metrics(primary_base_frame)

    representation_agreement = _representation_agreement(
        entries, simple_score, log_score, symbols, sector_by_symbol
    )
    binding_fraction = float(np.mean(np.any(np.abs(primary - baseline) > 1e-12, axis=1)))
    symbol_share = maximum_symbol_slot_share(primary)
    sector_share = maximum_sector_slot_share(primary, symbols, sector_by_symbol)
    turnover_increase = float(primary_base["mean_turnover"] or 0.0) / float(
        baseline_metrics["mean_turnover"] or 1.0
    ) - 1.0
    structural_thresholds = protocol["structural_gates"]
    structural_checks = {
        "representation_agreement_at_least_95pct": representation_agreement
        >= float(structural_thresholds["minimum_representation_agreement"]),
        "sector_cap_binds_often_enough": binding_fraction
        >= float(structural_thresholds["minimum_sector_cap_binding_fraction"]),
        "single_symbol_slot_share_within_cap": float(symbol_share["maximum"])
        <= float(structural_thresholds["maximum_single_symbol_slot_share"]),
        "sector_slot_share_within_cap": float(sector_share["maximum"])
        <= float(structural_thresholds["maximum_sector_slot_share_per_side"]) + 1e-12,
        "turnover_increase_within_limit": turnover_increase
        <= float(structural_thresholds["maximum_turnover_increase_vs_baseline"]),
        "all_targets_market_neutral": bool(np.allclose(primary.sum(axis=1), 0.0)),
        "all_targets_unit_gross": bool(np.allclose(np.abs(primary).sum(axis=1), 1.0)),
    }
    structural = {
        "representation_agreement": representation_agreement,
        "sector_cap_binding_fraction": binding_fraction,
        "maximum_symbol_slot_share": symbol_share,
        "maximum_sector_slot_share": sector_share,
        "turnover_increase_vs_baseline": turnover_increase,
        "checks": structural_checks,
        "passed": bool(all(structural_checks.values())),
    }

    historical_thresholds = protocol["historical_gates"]
    positive_segments = sum(float(item.get("mean") or 0.0) > 0.0 for item in segment_metrics.values())
    historical_checks = {
        "base_profit_factor_at_least_minimum": float(primary_base["profit_factor"] or 0.0)
        >= float(historical_thresholds["minimum_base_profit_factor"]),
        "stress_profit_factor_at_least_one": float(primary_stress["profit_factor"] or 0.0)
        >= float(historical_thresholds["minimum_stress_profit_factor"]),
        "base_pf_loss_vs_baseline_within_limit": float(primary_base["profit_factor"] or 0.0)
        >= float(baseline_metrics["profit_factor"] or 0.0)
        - float(historical_thresholds["maximum_base_pf_loss_vs_baseline"]),
        "drawdown_increase_vs_baseline_within_limit": abs(float(primary_base["maximum_drawdown"] or 0.0))
        <= abs(float(baseline_metrics["maximum_drawdown"] or 0.0))
        + float(historical_thresholds["maximum_drawdown_increase_vs_baseline"]),
        "positive_in_at_least_two_segments": positive_segments
        >= int(historical_thresholds["minimum_positive_chronological_segments"]),
    }
    historical = {
        "positive_segments": positive_segments,
        "segments": segment_metrics,
        "checks": historical_checks,
        "passed": bool(all(historical_checks.values())),
    }

    falsification_frame, falsification, observed_frame, _ = _falsification_rows(
        entries,
        primary,
        h4_open,
        delayed_open,
        funding_rates,
        delayed_funding_rates,
        protocol,
    )
    neighborhood_frame, neighborhood = _parameter_neighborhood(
        entries, variants, h4_open, funding_rates, protocol
    )
    regimes = _regime_labels(panels, entries, symbols)
    portfolio_frame, portfolio = _portfolio_increment(
        entries, baseline, primary, h4_open, funding_rates, regimes, protocol
    )

    gate_groups = {
        "structural": {"passed": structural["passed"], "checks": structural["checks"]},
        "historical_cost_and_segments": {"passed": historical["passed"], "checks": historical["checks"]},
        "falsification": {"passed": falsification["passed"], "checks": falsification["checks"]},
        "parameter_neighborhood": {"passed": neighborhood["passed"], "checks": neighborhood["checks"]},
        "portfolio_increment": {
            "passed": portfolio["passed"],
            "checks": {
                **portfolio["no_deterioration_checks"],
                "at_least_one_material_improvement": any(portfolio["improvement_checks"].values()),
            },
        },
    }
    passed = bool(all(group["passed"] for group in gate_groups.values()))
    decision = (
        "HISTORICALLY_SUPPORTED_RESEARCH_SHADOW_ONLY"
        if passed
        else "REJECT_AND_ARCHIVE_NO_RESCUE"
    )
    result: dict[str, Any] = {
        "schema": "momentum_sector_balance_evaluation_v1",
        "protocol_id": protocol["protocol_id"],
        "protocol_locked_before_pnl": True,
        "outcomes_opened": True,
        "history_cutoff_utc": OPENED_HISTORY_CUTOFF,
        "signal_count": len(entries),
        "universe": symbols,
        "funding_coverage": funding_coverage,
        "performance": {
            "baseline_base": baseline_metrics,
            "primary_base": primary_base,
            "primary_stress": primary_stress,
        },
        "structural": structural,
        "historical": historical,
        "falsification": falsification,
        "parameter_neighborhood": neighborhood,
        "portfolio_increment": portfolio,
        "gate_groups": gate_groups,
        "decision": decision,
        "independent_alpha_claim": False,
        "new_h_number": None,
        "formal_a_allowed": False,
        "production_effect": "NONE",
        "automatic_ordering": False,
        "prohibitions": protocol["prohibitions"],
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    falsification_frame.to_csv(output_dir / "falsification_trials.csv", index=False)
    neighborhood_frame.to_csv(output_dir / "parameter_neighborhood.csv", index=False)
    portfolio_frame.to_csv(output_dir / "portfolio_increment.csv", index=False)
    observed_frame.to_csv(output_dir / "primary_interval_returns.csv", index=False)
    _write_json(output_dir / "result.json", result)
    (output_dir / "RESULTS_CN.md").write_text(_markdown(result), encoding="utf-8")
    shutil.copy2(PROTOCOL_PATH, output_dir / PROTOCOL_PATH.name)

    artifact_hashes = {
        path.name: _sha256(path)
        for path in sorted(output_dir.iterdir())
        if path.is_file() and path.name != "SHA256SUMS.json"
    }
    _write_json(output_dir / "SHA256SUMS.json", artifact_hashes)

    if not passed:
        archive_dir = _archive_failure(output_dir, result)
        result["failure_archive"] = str(archive_dir)
        _write_json(output_dir / "result.json", result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--json", action="store_true", help="print the complete result")
    args = parser.parse_args()
    result = run(args.output_dir.resolve())
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, default=_json_default))
    else:
        print(
            json.dumps(
                {
                    "protocol_id": result["protocol_id"],
                    "decision": result["decision"],
                    "signal_count": result["signal_count"],
                    "failed_gate_groups": [
                        name for name, group in result["gate_groups"].items() if not group["passed"]
                    ],
                    "failure_archive": result.get("failure_archive"),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
