from __future__ import annotations

"""Return-blind H29 structure audit.

No future portfolio return, PnL, profit factor, win rate, or drawdown is read or
calculated. The audit checks whether dispersion widening is a bad state and
whether the frozen exposure sort is causal, representation-stable, distinct,
tradeable, and sufficiently diversified before any return is opened.
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
H28_DIR = ROOT / "HISTORY_PACKAGES_20260621" / "RESEARCH" / "h28_downside_market_beta_premium_v1"
for path in (DISCOVERY, H20_DIR, H28_DIR):
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
from h28_pre_pnl_audit import conditional_beta_asymmetry

CANDIDATE_ID = "H29_42D_CROSS_SECTIONAL_DISPERSION_SHOCK_EXPOSURE_PREMIUM_V1"
PRIMARY_LOOKBACK_HOURS = 24 * 42
NEIGHBOUR_LOOKBACK_HOURS = (24 * 28, 24 * 56)
TOP_BOTTOM_COUNT = 4
REFERENCE_MOMENTUM_HOURS = 24 * 14
REFERENCE_REVERSAL_HOURS = 24
REFERENCE_TAIL_HOURS = 24 * 7
MIN_REBALANCES = 100
MIN_BAD_STATE_NEGATIVE_FRACTION = 0.55
MAX_BAD_STATE_MEDIAN_MARKET_RETURN = 0.0
MIN_STD_MAD_FULL_SET_AGREEMENT = 0.75
MIN_STD_MAD_MEDIAN_RANK_CORRELATION = 0.65
MIN_STD_MAD_P10_RANK_CORRELATION = 0.25
MIN_NEIGHBOUR_MEDIAN_RANK_CORRELATION = 0.65
MIN_NEIGHBOUR_P10_RANK_CORRELATION = 0.25
MAX_EXISTING_FAMILY_WEIGHT_CORRELATION = 0.50
MAX_EXISTING_FAMILY_SAME_SIDE_OVERLAP = 0.25
MAX_RISK_PROXY_WEIGHT_CORRELATION = 0.75
MAX_MEAN_ONE_WAY_TURNOVER = 0.70
MAX_SINGLE_SYMBOL_SLOT_SHARE = 0.20


def dispersion_shocks(returns: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    standard = returns.std(axis=1, ddof=0).replace(0.0, np.nan)
    cross_median = returns.median(axis=1)
    absolute_deviation = returns.sub(cross_median, axis=0).abs()
    robust = (1.4826 * absolute_deviation.median(axis=1)).replace(0.0, np.nan)
    return np.log(standard).diff(), np.log(robust).diff()


def exposure_score(
    returns: pd.DataFrame,
    shock: pd.Series,
    timestamp: pd.Timestamp,
    lookback: int,
) -> pd.Series:
    history = returns.loc[returns.index < timestamp].tail(lookback)
    shock_history = shock.reindex(history.index)
    if len(history) < lookback or history.isna().any().any() or shock_history.isna().any():
        return pd.Series(index=ELIGIBLE_SYMBOLS, dtype=float)
    variance = float(shock_history.var(ddof=0))
    if variance <= 0.0:
        return pd.Series(index=ELIGIBLE_SYMBOLS, dtype=float)
    output: dict[str, float] = {}
    for symbol in ELIGIBLE_SYMBOLS:
        covariance = float(np.cov(history[symbol], shock_history, ddof=0)[0, 1])
        output[symbol] = -(covariance / variance)
    return pd.Series(output, dtype=float)


def metric_summary(values: list[float]) -> dict[str, float | int | None]:
    return {
        "observation_count": len(values),
        "median_rank_correlation": float(np.median(values)) if values else None,
        "p10_rank_correlation": float(np.quantile(values, 0.10)) if values else None,
    }


def build_audit(panels: MarketPanels) -> dict[str, Any]:
    close = panels.h1_close.loc[:, ELIGIBLE_SYMBOLS]
    returns = np.log(close).diff()
    market = returns.mean(axis=1)
    std_shock, mad_shock = dispersion_shocks(returns)

    valid_state = pd.concat(
        [std_shock.rename("shock"), market.rename("market")], axis=1
    ).dropna()
    top_threshold = float(valid_state["shock"].quantile(0.90))
    top_state = valid_state.loc[valid_state["shock"] >= top_threshold]
    bad_state_negative_fraction = float((top_state["market"] < 0.0).mean())
    bad_state_median_market_return = float(top_state["market"].median())

    idio, plain_beta, total_vol = factor_statistics(returns, PRIMARY_LOOKBACK_HOURS)
    momentum = np.log(close / close.shift(REFERENCE_MOMENTUM_HOURS)).shift(1)
    reversal = -np.log(close / close.shift(REFERENCE_REVERSAL_HOURS)).shift(1)
    tail = expected_shortfall_score(returns, REFERENCE_TAIL_HOURS)
    lottery = daily_max_return_score(close, 28)
    h19 = h19_sector_weights(close)

    candidate_rows: list[np.ndarray] = []
    references: dict[str, list[np.ndarray]] = {
        "14_day_cross_sectional_momentum": [],
        "24h_cross_sectional_reversal": [],
        "H19_sector_rank_momentum": [],
        "H20_42day_idiosyncratic_volatility_proxy": [],
        "H28_downside_beta_asymmetry": [],
        "42day_plain_market_beta": [],
        "42day_total_volatility": [],
        "7day_expected_shortfall": [],
        "28day_max_daily_return_lottery": [],
    }
    timestamps: list[pd.Timestamp] = []
    candidate_longs: list[list[str]] = []
    candidate_shorts: list[list[str]] = []
    std_mad_full_agreement: list[bool] = []
    std_mad_correlations: list[float] = []
    neighbour_correlations: dict[int, list[float]] = {
        lookback: [] for lookback in NEIGHBOUR_LOOKBACK_HOURS
    }
    exposure_spreads: list[float] = []

    for timestamp in schedule_index(close.index):
        current_time = pd.Timestamp(timestamp)
        primary = exposure_score(returns, std_shock, current_time, PRIMARY_LOOKBACK_HOURS)
        robust = exposure_score(returns, mad_shock, current_time, PRIMARY_LOOKBACK_HOURS)
        neighbours = {
            lookback: exposure_score(returns, std_shock, current_time, lookback)
            for lookback in NEIGHBOUR_LOOKBACK_HOURS
        }
        h28 = conditional_beta_asymmetry(
            returns, current_time, PRIMARY_LOOKBACK_HOURS, "equal_weight"
        )
        current_references = {
            "momentum": momentum.loc[timestamp],
            "reversal": reversal.loc[timestamp],
            "idio": idio.loc[timestamp],
            "h28": h28,
            "plain_beta": plain_beta.loc[timestamp],
            "total_vol": total_vol.loc[timestamp],
            "tail": tail.loc[timestamp],
            "lottery": lottery.loc[timestamp],
        }
        if not finite_complete(primary) or not finite_complete(robust):
            continue
        if not all(finite_complete(score) for score in neighbours.values()):
            continue
        if not all(finite_complete(score) for score in current_references.values()):
            continue

        candidate_weight, longs, shorts = rank_weights(
            primary, low_is_long=False, count=TOP_BOTTOM_COUNT
        )
        robust_weight, robust_longs, robust_shorts = rank_weights(
            robust, low_is_long=False, count=TOP_BOTTOM_COUNT
        )
        if not longs or not shorts or not robust_longs or not robust_shorts:
            continue

        candidate_rows.append(candidate_weight)
        timestamps.append(current_time)
        candidate_longs.append(longs)
        candidate_shorts.append(shorts)
        std_mad_full_agreement.append(
            set(longs) == set(robust_longs) and set(shorts) == set(robust_shorts)
        )
        correlation = rank_correlation(primary, robust)
        if correlation is not None:
            std_mad_correlations.append(correlation)
        for lookback, score in neighbours.items():
            value = rank_correlation(primary, score)
            if value is not None:
                neighbour_correlations[lookback].append(value)
        exposure_spreads.append(float(primary.loc[longs].mean() - primary.loc[shorts].mean()))

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
        references["H28_downside_beta_asymmetry"].append(
            rank_weights(current_references["h28"], low_is_long=False)[0]
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
        "std_mad_full_set_agreement": float(np.mean(std_mad_full_agreement))
        if std_mad_full_agreement
        else 0.0,
        "std_mad_rank": metric_summary(std_mad_correlations),
    }
    neighbour_summary = {
        str(lookback // 24): metric_summary(values)
        for lookback, values in neighbour_correlations.items()
    }

    existing_names = (
        "14_day_cross_sectional_momentum",
        "24h_cross_sectional_reversal",
        "H19_sector_rank_momentum",
    )
    risk_proxy_names = (
        "H20_42day_idiosyncratic_volatility_proxy",
        "H28_downside_beta_asymmetry",
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
    rank_summary = representation["std_mad_rank"]
    representation_passed = bool(
        representation["std_mad_full_set_agreement"] >= MIN_STD_MAD_FULL_SET_AGREEMENT
        and rank_summary["median_rank_correlation"] is not None
        and float(rank_summary["median_rank_correlation"])
        >= MIN_STD_MAD_MEDIAN_RANK_CORRELATION
        and rank_summary["p10_rank_correlation"] is not None
        and float(rank_summary["p10_rank_correlation"])
        >= MIN_STD_MAD_P10_RANK_CORRELATION
    )
    neighbour_stable = all(
        values
        and float(np.median(values)) >= MIN_NEIGHBOUR_MEDIAN_RANK_CORRELATION
        and float(np.quantile(values, 0.10)) >= MIN_NEIGHBOUR_P10_RANK_CORRELATION
        for values in neighbour_correlations.values()
    )
    bad_state_passed = bool(
        bad_state_negative_fraction >= MIN_BAD_STATE_NEGATIVE_FRACTION
        and bad_state_median_market_return <= MAX_BAD_STATE_MEDIAN_MARKET_RETURN
    )
    concentration_passed = bool(
        float(long_concentration["leader_share"]) <= MAX_SINGLE_SYMBOL_SLOT_SHARE
        and float(short_concentration["leader_share"]) <= MAX_SINGLE_SYMBOL_SLOT_SHARE
    )
    all_pass = bool(
        len(timestamps) >= MIN_REBALANCES
        and bad_state_passed
        and representation_passed
        and neighbour_stable
        and not duplicate_existing
        and not duplicate_risk_proxy
        and turnover <= MAX_MEAN_ONE_WAY_TURNOVER
        and concentration_passed
    )

    result = {
        "schema": "h29_pre_pnl_audit_v1",
        "candidate_id": CANDIDATE_ID,
        "interpretation": "Return-blind structure audit only; no future return or PnL metric calculated.",
        "candidate_rule": {
            "primary_dispersion": "cross-sectional standard deviation of hourly log returns",
            "dispersion_shock": "first difference of log dispersion",
            "characteristic": "negative beta of asset return to dispersion shock",
            "lookback_hours": PRIMARY_LOOKBACK_HOURS,
            "feature_lag_hours": 1,
            "rebalance": "Monday 00:00 UTC",
            "long": "four highest loss-exposure scores",
            "short": "four lowest loss-exposure scores",
            "future_outcomes_opened": False,
            "parameter_search_count": 0,
        },
        "bad_state_semantics": {
            "top_decile_observations": int(len(top_state)),
            "top_decile_negative_market_fraction": bad_state_negative_fraction,
            "top_decile_median_market_return": bad_state_median_market_return,
        },
        "coverage": {
            "rebalance_count": len(timestamps),
            "first_rebalance_utc": timestamps[0].isoformat() if timestamps else None,
            "last_rebalance_utc": timestamps[-1].isoformat() if timestamps else None,
            "mean_one_way_turnover": turnover,
            "median_long_minus_short_loss_exposure": float(np.median(exposure_spreads))
            if exposure_spreads
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
            "minimum_bad_state_top_decile_negative_market_fraction": MIN_BAD_STATE_NEGATIVE_FRACTION,
            "maximum_bad_state_top_decile_median_market_return": MAX_BAD_STATE_MEDIAN_MARKET_RETURN,
            "minimum_std_mad_full_set_agreement": MIN_STD_MAD_FULL_SET_AGREEMENT,
            "minimum_std_mad_median_rank_correlation": MIN_STD_MAD_MEDIAN_RANK_CORRELATION,
            "minimum_std_mad_p10_rank_correlation": MIN_STD_MAD_P10_RANK_CORRELATION,
            "minimum_neighbour_median_rank_correlation": MIN_NEIGHBOUR_MEDIAN_RANK_CORRELATION,
            "minimum_neighbour_p10_rank_correlation": MIN_NEIGHBOUR_P10_RANK_CORRELATION,
            "maximum_existing_family_weight_correlation": MAX_EXISTING_FAMILY_WEIGHT_CORRELATION,
            "maximum_existing_family_same_side_overlap": MAX_EXISTING_FAMILY_SAME_SIDE_OVERLAP,
            "maximum_risk_proxy_weight_correlation": MAX_RISK_PROXY_WEIGHT_CORRELATION,
            "maximum_mean_one_way_turnover": MAX_MEAN_ONE_WAY_TURNOVER,
            "maximum_single_symbol_slot_share": MAX_SINGLE_SYMBOL_SLOT_SHARE,
            "bad_state_passed": bad_state_passed,
            "representation_passed": representation_passed,
            "neighbour_stable": neighbour_stable,
            "duplicate_existing_family": duplicate_existing,
            "duplicate_risk_proxy": duplicate_risk_proxy,
            "concentration_passed": concentration_passed,
            "all_pass": all_pass,
        },
        "decision": "FREEZE_PNL_PROTOCOL" if all_pass else "REJECT_H29_BEFORE_PNL_NO_RESCUE",
        "future_returns_opened": False,
        "pnl_opened": False,
        "formal_a": False,
        "automatic_ordering": False,
        "production_effect": "NONE",
    }
    return result


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
