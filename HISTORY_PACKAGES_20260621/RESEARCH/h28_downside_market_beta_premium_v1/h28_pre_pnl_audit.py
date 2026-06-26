from __future__ import annotations

"""Return-blind H28 structure audit.

The script never reads or calculates future portfolio returns, PnL, profit
factor, win rate, or drawdown. It only checks causal construction,
representation invariance, lookback-neighbour stability, family overlap,
turnover, and slot concentration for the frozen downside-beta-asymmetry sort.
"""

import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
DISCOVERY = ROOT / "HISTORY_PACKAGES_20260621" / "RESEARCH" / "local_only_hypothesis_discovery_v1"
H20_DIR = ROOT / "HISTORY_PACKAGES_20260621" / "RESEARCH" / "h20_low_idiosyncratic_volatility"
for path in (DISCOVERY, H20_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from momentum_overlay_common import MarketPanels, load_panels
from h20_pre_pnl_audit import (
    ELIGIBLE_SYMBOLS,
    comparison,
    daily_max_return_score,
    expected_shortfall_score,
    factor_statistics,
    finite_complete,
    h19_sector_weights,
    mean_one_way_turnover,
    rank_correlation,
    rank_weights,
    schedule_index,
    slot_concentration,
)

CANDIDATE_ID = "H28_42D_DOWNSIDE_MARKET_BETA_ASYMMETRY_PREMIUM_V1"
PRIMARY_LOOKBACK_HOURS = 24 * 42
NEIGHBOUR_LOOKBACK_HOURS = (24 * 28, 24 * 56)
TOP_BOTTOM_COUNT = 4
MIN_CONDITIONAL_OBSERVATIONS = 100
REFERENCE_MOMENTUM_HOURS = 24 * 14
REFERENCE_REVERSAL_HOURS = 24
REFERENCE_TAIL_HOURS = 24 * 7
MIN_REBALANCES = 100
MIN_LOG_SIMPLE_FULL_SET_AGREEMENT = 0.90
MIN_BTC_PROXY_MEDIAN_RANK_CORRELATION = 0.65
MIN_BTC_PROXY_P10_RANK_CORRELATION = 0.25
MIN_NEIGHBOUR_MEDIAN_RANK_CORRELATION = 0.65
MIN_NEIGHBOUR_P10_RANK_CORRELATION = 0.25
MAX_EXISTING_FAMILY_WEIGHT_CORRELATION = 0.50
MAX_EXISTING_FAMILY_SAME_SIDE_OVERLAP = 0.25
MAX_RISK_PROXY_WEIGHT_CORRELATION = 0.75
MAX_MEAN_ONE_WAY_TURNOVER = 0.70
MAX_SINGLE_SYMBOL_SLOT_SHARE = 0.20


def conditional_beta_asymmetry(
    returns: pd.DataFrame,
    timestamp: pd.Timestamp,
    lookback: int,
    benchmark: str,
) -> pd.Series:
    history = returns.loc[returns.index < timestamp].tail(lookback)
    if len(history) < lookback or history.isna().any().any():
        return pd.Series(index=ELIGIBLE_SYMBOLS, dtype=float)
    market = history.mean(axis=1) if benchmark == "equal_weight" else history[benchmark]
    negative = market < 0.0
    positive = market > 0.0
    if int(negative.sum()) < MIN_CONDITIONAL_OBSERVATIONS or int(positive.sum()) < MIN_CONDITIONAL_OBSERVATIONS:
        return pd.Series(index=ELIGIBLE_SYMBOLS, dtype=float)
    down_variance = float(market.loc[negative].var(ddof=0))
    up_variance = float(market.loc[positive].var(ddof=0))
    if down_variance <= 0.0 or up_variance <= 0.0:
        return pd.Series(index=ELIGIBLE_SYMBOLS, dtype=float)
    output: dict[str, float] = {}
    for symbol in ELIGIBLE_SYMBOLS:
        down_covariance = float(
            np.cov(history.loc[negative, symbol], market.loc[negative], ddof=0)[0, 1]
        )
        up_covariance = float(
            np.cov(history.loc[positive, symbol], market.loc[positive], ddof=0)[0, 1]
        )
        output[symbol] = down_covariance / down_variance - up_covariance / up_variance
    return pd.Series(output, dtype=float)


def summary(values: list[float]) -> dict[str, float | int | None]:
    return {
        "observation_count": len(values),
        "median_rank_correlation": float(np.median(values)) if values else None,
        "p10_rank_correlation": float(np.quantile(values, 0.10)) if values else None,
    }


def build_audit(panels: MarketPanels) -> dict[str, Any]:
    close = panels.h1_close.loc[:, ELIGIBLE_SYMBOLS]
    log_returns = np.log(close).diff()
    simple_returns = close.pct_change()

    idio, plain_beta, total_vol = factor_statistics(log_returns, PRIMARY_LOOKBACK_HOURS)
    momentum = np.log(close / close.shift(REFERENCE_MOMENTUM_HOURS)).shift(1)
    reversal = -np.log(close / close.shift(REFERENCE_REVERSAL_HOURS)).shift(1)
    tail = expected_shortfall_score(log_returns, REFERENCE_TAIL_HOURS)
    lottery = daily_max_return_score(close, 28)
    h19 = h19_sector_weights(close)

    candidate_rows: list[np.ndarray] = []
    references: dict[str, list[np.ndarray]] = {
        "14_day_cross_sectional_momentum": [],
        "24h_cross_sectional_reversal": [],
        "H19_sector_rank_momentum": [],
        "H20_42day_idiosyncratic_volatility_proxy": [],
        "42day_plain_market_beta": [],
        "42day_total_volatility": [],
        "7day_expected_shortfall": [],
        "28day_max_daily_return_lottery": [],
    }
    timestamps: list[pd.Timestamp] = []
    candidate_longs: list[list[str]] = []
    candidate_shorts: list[list[str]] = []
    log_simple_full_agreement: list[bool] = []
    log_simple_rank_correlations: list[float] = []
    btc_proxy_rank_correlations: list[float] = []
    neighbour_correlations: dict[int, list[float]] = {
        lookback: [] for lookback in NEIGHBOUR_LOOKBACK_HOURS
    }
    asymmetry_spreads: list[float] = []

    for timestamp in schedule_index(close.index):
        primary = conditional_beta_asymmetry(
            log_returns, pd.Timestamp(timestamp), PRIMARY_LOOKBACK_HOURS, "equal_weight"
        )
        simple = conditional_beta_asymmetry(
            simple_returns, pd.Timestamp(timestamp), PRIMARY_LOOKBACK_HOURS, "equal_weight"
        )
        btc_proxy = conditional_beta_asymmetry(
            log_returns, pd.Timestamp(timestamp), PRIMARY_LOOKBACK_HOURS, "BTC-USDT-SWAP"
        )
        neighbours = {
            lookback: conditional_beta_asymmetry(
                log_returns, pd.Timestamp(timestamp), lookback, "equal_weight"
            )
            for lookback in NEIGHBOUR_LOOKBACK_HOURS
        }
        current_references = {
            "momentum": momentum.loc[timestamp],
            "reversal": reversal.loc[timestamp],
            "idio": idio.loc[timestamp],
            "plain_beta": plain_beta.loc[timestamp],
            "total_vol": total_vol.loc[timestamp],
            "tail": tail.loc[timestamp],
            "lottery": lottery.loc[timestamp],
        }
        if not finite_complete(primary) or not finite_complete(simple) or not finite_complete(btc_proxy):
            continue
        if not all(finite_complete(score) for score in neighbours.values()):
            continue
        if not all(finite_complete(score) for score in current_references.values()):
            continue

        candidate_weight, longs, shorts = rank_weights(
            primary, low_is_long=False, count=TOP_BOTTOM_COUNT
        )
        simple_weight, simple_longs, simple_shorts = rank_weights(
            simple, low_is_long=False, count=TOP_BOTTOM_COUNT
        )
        if not longs or not shorts or not simple_longs or not simple_shorts:
            continue

        candidate_rows.append(candidate_weight)
        timestamps.append(pd.Timestamp(timestamp))
        candidate_longs.append(longs)
        candidate_shorts.append(shorts)
        log_simple_full_agreement.append(
            set(longs) == set(simple_longs) and set(shorts) == set(simple_shorts)
        )
        log_simple_correlation = rank_correlation(primary, simple)
        if log_simple_correlation is not None:
            log_simple_rank_correlations.append(log_simple_correlation)
        btc_correlation = rank_correlation(primary, btc_proxy)
        if btc_correlation is not None:
            btc_proxy_rank_correlations.append(btc_correlation)
        for lookback, score in neighbours.items():
            value = rank_correlation(primary, score)
            if value is not None:
                neighbour_correlations[lookback].append(value)
        asymmetry_spreads.append(float(primary.loc[longs].mean() - primary.loc[shorts].mean()))

        references["14_day_cross_sectional_momentum"].append(
            rank_weights(current_references["momentum"], low_is_long=False)[0]
        )
        references["24h_cross_sectional_reversal"].append(
            rank_weights(current_references["reversal"], low_is_long=False)[0]
        )
        references["H19_sector_rank_momentum"].append(
            h19.loc[timestamp].to_numpy(dtype=float)
        )
        references["H20_42day_idiosyncratic_volatility_proxy"].append(
            rank_weights(current_references["idio"], low_is_long=True)[0]
        )
        references["42day_plain_market_beta"].append(
            rank_weights(current_references["plain_beta"], low_is_long=False)[0]
        )
        references["42day_total_volatility"].append(
            rank_weights(current_references["total_vol"], low_is_long=False)[0]
        )
        references["7day_expected_shortfall"].append(
            rank_weights(current_references["tail"], low_is_long=False)[0]
        )
        references["28day_max_daily_return_lottery"].append(
            rank_weights(current_references["lottery"], low_is_long=False)[0]
        )

    candidate = np.asarray(candidate_rows, dtype=float)
    comparisons = {
        name: comparison(candidate, np.asarray(rows, dtype=float))
        for name, rows in references.items()
    }
    turnover = mean_one_way_turnover(candidate)
    long_concentration = slot_concentration(candidate_longs)
    short_concentration = slot_concentration(candidate_shorts)

    representation = {
        "observations": len(timestamps),
        "log_simple_full_set_agreement": float(np.mean(log_simple_full_agreement))
        if log_simple_full_agreement
        else 0.0,
        "log_simple_rank": summary(log_simple_rank_correlations),
        "equal_weight_vs_btc_proxy_rank": summary(btc_proxy_rank_correlations),
    }
    neighbour_summary = {
        str(lookback // 24): summary(values)
        for lookback, values in neighbour_correlations.items()
    }

    existing_names = (
        "14_day_cross_sectional_momentum",
        "24h_cross_sectional_reversal",
        "H19_sector_rank_momentum",
    )
    risk_proxy_names = (
        "H20_42day_idiosyncratic_volatility_proxy",
        "42day_plain_market_beta",
        "42day_total_volatility",
        "7day_expected_shortfall",
        "28day_max_daily_return_lottery",
    )
    duplicate_existing = any(
        abs(float(comparisons[name]["weight_vector_correlation"]))
        > MAX_EXISTING_FAMILY_WEIGHT_CORRELATION
        or float(comparisons[name]["mean_same_side_overlap_fraction_of_candidate"])
        > MAX_EXISTING_FAMILY_SAME_SIDE_OVERLAP
        for name in existing_names
    )
    duplicate_risk_proxy = any(
        abs(float(comparisons[name]["weight_vector_correlation"]))
        > MAX_RISK_PROXY_WEIGHT_CORRELATION
        for name in risk_proxy_names
    )
    btc_summary = representation["equal_weight_vs_btc_proxy_rank"]
    btc_proxy_stable = bool(
        btc_summary["median_rank_correlation"] is not None
        and float(btc_summary["median_rank_correlation"])
        >= MIN_BTC_PROXY_MEDIAN_RANK_CORRELATION
        and btc_summary["p10_rank_correlation"] is not None
        and float(btc_summary["p10_rank_correlation"])
        >= MIN_BTC_PROXY_P10_RANK_CORRELATION
    )
    neighbour_stable = all(
        values
        and float(np.median(values)) >= MIN_NEIGHBOUR_MEDIAN_RANK_CORRELATION
        and float(np.quantile(values, 0.10)) >= MIN_NEIGHBOUR_P10_RANK_CORRELATION
        for values in neighbour_correlations.values()
    )
    representation_passed = bool(
        representation["log_simple_full_set_agreement"]
        >= MIN_LOG_SIMPLE_FULL_SET_AGREEMENT
        and btc_proxy_stable
    )
    concentration_passed = bool(
        float(long_concentration["leader_share"]) <= MAX_SINGLE_SYMBOL_SLOT_SHARE
        and float(short_concentration["leader_share"]) <= MAX_SINGLE_SYMBOL_SLOT_SHARE
    )
    all_pass = bool(
        len(timestamps) >= MIN_REBALANCES
        and representation_passed
        and neighbour_stable
        and not duplicate_existing
        and not duplicate_risk_proxy
        and turnover <= MAX_MEAN_ONE_WAY_TURNOVER
        and concentration_passed
    )

    return {
        "schema": "h28_pre_pnl_audit_v1",
        "candidate_id": CANDIDATE_ID,
        "interpretation": "Return-blind structure audit only; no future return or PnL metric calculated.",
        "candidate_rule": {
            "market_factor": "equal-weight hourly return of the 18-symbol mature OKX perpetual universe",
            "characteristic": "negative-market conditional beta minus positive-market conditional beta",
            "lookback_hours": PRIMARY_LOOKBACK_HOURS,
            "feature_lag_hours": 1,
            "rebalance": "Monday 00:00 UTC",
            "long": "four highest downside-beta-asymmetry symbols",
            "short": "four lowest downside-beta-asymmetry symbols",
            "future_outcomes_opened": False,
            "parameter_search_count": 0,
        },
        "coverage": {
            "rebalance_count": len(timestamps),
            "first_rebalance_utc": timestamps[0].isoformat() if timestamps else None,
            "last_rebalance_utc": timestamps[-1].isoformat() if timestamps else None,
            "mean_one_way_turnover": turnover,
            "median_long_minus_short_asymmetry": float(np.median(asymmetry_spreads))
            if asymmetry_spreads
            else None,
        },
        "representation_invariance": representation,
        "neighbour_stability": neighbour_summary,
        "comparisons": comparisons,
        "selection_concentration": {
            "long_slots": long_concentration,
            "short_slots": short_concentration,
        },
        "gates": {
            "minimum_rebalances": MIN_REBALANCES,
            "minimum_log_simple_full_set_agreement": MIN_LOG_SIMPLE_FULL_SET_AGREEMENT,
            "minimum_btc_proxy_median_rank_correlation": MIN_BTC_PROXY_MEDIAN_RANK_CORRELATION,
            "minimum_btc_proxy_p10_rank_correlation": MIN_BTC_PROXY_P10_RANK_CORRELATION,
            "minimum_neighbour_median_rank_correlation": MIN_NEIGHBOUR_MEDIAN_RANK_CORRELATION,
            "minimum_neighbour_p10_rank_correlation": MIN_NEIGHBOUR_P10_RANK_CORRELATION,
            "maximum_existing_family_weight_correlation": MAX_EXISTING_FAMILY_WEIGHT_CORRELATION,
            "maximum_existing_family_same_side_overlap": MAX_EXISTING_FAMILY_SAME_SIDE_OVERLAP,
            "maximum_risk_proxy_weight_correlation": MAX_RISK_PROXY_WEIGHT_CORRELATION,
            "maximum_mean_one_way_turnover": MAX_MEAN_ONE_WAY_TURNOVER,
            "maximum_single_symbol_slot_share": MAX_SINGLE_SYMBOL_SLOT_SHARE,
            "representation_passed": representation_passed,
            "btc_proxy_stable": btc_proxy_stable,
            "neighbour_stable": neighbour_stable,
            "duplicate_existing_family": duplicate_existing,
            "duplicate_risk_proxy": duplicate_risk_proxy,
            "concentration_passed": concentration_passed,
            "all_pass": all_pass,
        },
        "decision": "FREEZE_PNL_PROTOCOL" if all_pass else "REJECT_H28_BEFORE_PNL_NO_RESCUE",
        "future_returns_opened": False,
        "pnl_opened": False,
        "formal_a": False,
        "automatic_ordering": False,
        "production_effect": "NONE",
    }


def main() -> int:
    result = build_audit(load_panels())
    output_path = Path(__file__).with_name("PRE_PNL_AUDIT_RESULT.json")
    output_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
