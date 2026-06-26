from __future__ import annotations

"""Update the isolated forward ledger for the frozen three-day momentum cadence.

This script records research evidence only. It never changes formal signals,
notifications, leverage, approved manifests, accounts, positions, or orders.
"""

import hashlib
import json
import math
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = PROJECT_ROOT.parents[1]
HISTORY_RESEARCH = (
    WORKSPACE_ROOT
    / "HISTORY_PACKAGES_20260621"
    / "RESEARCH"
    / "local_only_hypothesis_discovery_v1"
)
if str(HISTORY_RESEARCH) not in sys.path:
    sys.path.insert(0, str(HISTORY_RESEARCH))

import momentum_forward_shadow_observations as parent_observations  # noqa: E402
import momentum_overlay_common as common  # noqa: E402

from okx_signal_system.research.fixed_cadence_momentum import (  # noqa: E402
    fixed_cadence_hysteresis_weights,
    next_refresh_at_or_after,
    staggered_cadence_hysteresis_weights,
)

PROTOCOL_PATH = PROJECT_ROOT / "config" / "research_protocols" / "momentum_fixed_3d_refresh_v1.json"
LEDGER_PATH = PROJECT_ROOT / "outputs" / "momentum_fixed_3d_forward_ledger.json"
STATUS_PATH = PROJECT_ROOT / "outputs" / "momentum_fixed_3d_forward_status.json"
EVIDENCE_DIR = PROJECT_ROOT / "outputs" / "momentum_fixed_3d_forward_evidence"
VARIANT = "fixed_3d_refresh_hysteresis_4_in_6_out"
ADDITIONAL_CODE_PATHS: list[Path] = []


def _now_text() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON object required: {path}")
    return payload


def _load_optional_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        return _read_json(path)
    except (OSError, json.JSONDecodeError, ValueError):
        return None


def _canonical_json_bytes(payload: Any) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    _atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def _profit_factor(values: np.ndarray) -> float | None:
    positive = float(values[values > 0.0].sum())
    negative = float(-values[values < 0.0].sum())
    if negative == 0.0:
        return None if positive == 0.0 else float("inf")
    return positive / negative


def _performance(records: list[dict[str, Any]], field: str) -> dict[str, Any]:
    closed = [item for item in records if bool(item.get("closed")) and item.get(field) is not None]
    if not closed:
        return {
            "closed_count": 0,
            "profit_factor": None,
            "total_return": None,
            "maximum_drawdown": None,
            "win_rate": None,
            "mean_return": None,
        }
    values = np.asarray([float(item[field]) for item in closed], dtype=float)
    equity = np.cumprod(1.0 + values)
    peaks = np.maximum.accumulate(np.concatenate([[1.0], equity]))[1:]
    drawdown = equity / peaks - 1.0
    return {
        "closed_count": int(len(values)),
        "profit_factor": _profit_factor(values),
        "total_return": float(equity[-1] - 1.0),
        "maximum_drawdown": float(drawdown.min()),
        "win_rate": float(np.mean(values > 0.0)),
        "mean_return": float(values.mean()),
    }


def _daily_signal_frame(
    panels: common.MarketPanels,
    symbols: list[str],
    *,
    formation_bars_4h: int,
) -> pd.DataFrame:
    closes = panels.h4_close.loc[:, symbols]
    score = closes / closes.shift(formation_bars_4h) - 1.0
    rows: list[dict[str, Any]] = []
    for signal_time in score.index[score.index.hour == 0]:
        current = score.loc[signal_time]
        if not np.isfinite(current.to_numpy(dtype=float)).all():
            continue
        rows.append(
            {
                "signal_bar_open_utc": signal_time,
                "detected_at_utc": signal_time + pd.Timedelta(hours=4),
                "entry_utc": signal_time + pd.Timedelta(hours=4),
                "scores": {symbol: float(current[symbol]) for symbol in symbols},
            }
        )
    return pd.DataFrame(rows)


def _positive_share(values: dict[str, float]) -> float | None:
    positive = [value for value in values.values() if value > 0.0]
    total = float(sum(positive))
    if total <= 0.0:
        return None
    return float(max(positive) / total)


def _concentration(records: list[dict[str, Any]]) -> dict[str, Any]:
    closed = [item for item in records if bool(item.get("closed"))]
    symbol_totals: dict[str, float] = {}
    month_totals: dict[str, float] = {}
    profitable_refreshes: list[float] = []
    for item in closed:
        base_return = float(item.get("base_net_return") or 0.0)
        month = str(item.get("contribution_month_utc") or "unknown")
        month_totals[month] = month_totals.get(month, 0.0) + base_return
        if base_return > 0.0:
            profitable_refreshes.append(base_return)
        contributions = item.get("symbol_contributions")
        if isinstance(contributions, dict):
            for symbol, detail in contributions.items():
                if not isinstance(detail, dict):
                    continue
                value = float(detail.get("base_net_contribution") or 0.0)
                symbol_totals[str(symbol)] = symbol_totals.get(str(symbol), 0.0) + value

    positive_total = float(sum(profitable_refreshes))
    top5_share = None
    if positive_total > 0.0:
        top5_share = float(sum(sorted(profitable_refreshes, reverse=True)[:5]) / positive_total)
    return {
        "single_symbol_positive_contribution_share": _positive_share(symbol_totals),
        "single_positive_month_share": _positive_share(month_totals),
        "top_5_profitable_refreshes_positive_contribution_share": top5_share,
        "symbol_net_contribution": symbol_totals,
        "month_net_contribution": month_totals,
    }


def _drifted_weights(
    previous_weights: np.ndarray,
    previous_entry: pd.Timestamp,
    current_entry: pd.Timestamp,
    panels: common.MarketPanels,
    symbols: list[str],
) -> np.ndarray:
    if previous_entry not in panels.m15_open.index or current_entry not in panels.m15_open.index:
        raise ValueError("missing 15-minute open required for turnover accounting")
    previous_prices = panels.m15_open.loc[previous_entry, symbols].to_numpy(dtype=float)
    current_prices = panels.m15_open.loc[current_entry, symbols].to_numpy(dtype=float)
    asset_return = current_prices / previous_prices - 1.0
    equity_factor = 1.0 + float(np.dot(previous_weights, asset_return))
    if equity_factor <= 0.0:
        return previous_weights.copy()
    return previous_weights * (1.0 + asset_return) / equity_factor


def _reference_prices(
    entry: pd.Timestamp,
    weights: np.ndarray,
    panels: common.MarketPanels,
    symbols: list[str],
) -> dict[str, dict[str, float | None]]:
    result: dict[str, dict[str, float | None]] = {}
    for index, symbol in enumerate(symbols):
        if weights[index] == 0.0:
            continue
        values: dict[str, float | None] = {}
        for label, offset in (("entry_0m_open", 0), ("reference_15m_open", 15), ("reference_30m_open", 30), ("reference_60m_open", 60)):
            timestamp = entry + pd.Timedelta(minutes=offset)
            if timestamp in panels.m15_open.index:
                value = float(panels.m15_open.loc[timestamp, symbol])
                values[label] = value if math.isfinite(value) and value > 0.0 else None
            else:
                values[label] = None
        result[symbol] = values
    return result


def _record_for_refresh(
    *,
    signal: pd.Series,
    signal_index: int,
    weights: np.ndarray,
    previous_weights: np.ndarray,
    previous_entry: pd.Timestamp | None,
    panels: common.MarketPanels,
    protocol: dict[str, Any],
    prior: dict[str, Any] | None,
    recovery_run: bool,
    symbols: list[str],
) -> dict[str, Any]:
    entry = pd.Timestamp(signal["entry_utc"])
    exit_time = entry + pd.Timedelta(hours=int(protocol["signal"]["refresh_interval_hours"]))
    drifted = (
        np.zeros_like(weights)
        if previous_entry is None
        else _drifted_weights(previous_weights, previous_entry, entry, panels, symbols)
    )
    weight_change = weights - drifted
    turnover = float(np.abs(weight_change).sum())
    gross_exposure = float(np.abs(weights).sum())
    cost = protocol["costs"]
    base_transaction = turnover * float(cost["historical_base_one_way_per_unit_turnover"])
    stress_transaction = turnover * float(cost["historical_stress_one_way_per_unit_turnover"])
    base_funding = (
        gross_exposure
        * float(cost["forward_base_adverse_funding_reserve_per_8h_full_gross"])
        * int(cost["funding_intervals_per_refresh"])
    )
    stress_funding = (
        gross_exposure
        * float(cost["forward_stress_adverse_funding_reserve_per_8h_full_gross"])
        * int(cost["funding_intervals_per_refresh"])
    )
    prior = prior or {}
    record: dict[str, Any] = {
        "signal_bar_open_utc": pd.Timestamp(signal["signal_bar_open_utc"]).isoformat(),
        "detected_and_entry_utc": entry.isoformat(),
        "exit_utc": exit_time.isoformat(),
        "signal_index": int(signal_index),
        "longs": [symbols[index] for index, value in enumerate(weights) if value > 0.0],
        "shorts": [symbols[index] for index, value in enumerate(weights) if value < 0.0],
        "weights": {
            symbols[index]: float(value)
            for index, value in enumerate(weights)
            if float(value) != 0.0
        },
        "momentum_14d": {symbol: float(signal["scores"][symbol]) for symbol in symbols},
        "reference_prices": _reference_prices(entry, weights, panels, symbols),
        "turnover": turnover,
        "gross_exposure": gross_exposure,
        "base_cost_breakdown": {
            "transaction": base_transaction,
            "adverse_funding_reserve": base_funding,
            "total": base_transaction + base_funding,
        },
        "stress_cost_breakdown": {
            "transaction": stress_transaction,
            "adverse_funding_reserve": stress_funding,
            "total": stress_transaction + stress_funding,
        },
        "funding_source": "FROZEN_ADVERSE_RESERVE_FOR_FORWARD_EVIDENCE",
        "first_recorded_at_utc": str(prior.get("first_recorded_at_utc") or _now_text()),
        "last_refreshed_at_utc": _now_text(),
        "recovery_backfill": bool(prior.get("recovery_backfill", recovery_run)),
        "recording_mode": "POWER_RECOVERY_BACKFILL" if recovery_run else "NORMAL_FIXED_CADENCE_CAPTURE",
        "closed": False,
    }

    latest_closed = pd.Timestamp(panels.m15_close.index[-1])
    if exit_time in panels.m15_open.index and exit_time <= latest_closed:
        entry_prices = panels.m15_open.loc[entry, symbols].to_numpy(dtype=float)
        exit_prices = panels.m15_open.loc[exit_time, symbols].to_numpy(dtype=float)
        asset_return = exit_prices / entry_prices - 1.0
        gross_contributions = weights * asset_return
        symbol_contributions: dict[str, dict[str, Any]] = {}
        for index, symbol in enumerate(symbols):
            if weights[index] == 0.0 and weight_change[index] == 0.0:
                continue
            base_transaction_symbol = abs(float(weight_change[index])) * float(
                cost["historical_base_one_way_per_unit_turnover"]
            )
            stress_transaction_symbol = abs(float(weight_change[index])) * float(
                cost["historical_stress_one_way_per_unit_turnover"]
            )
            base_funding_symbol = (
                abs(float(weights[index]))
                * float(cost["forward_base_adverse_funding_reserve_per_8h_full_gross"])
                * int(cost["funding_intervals_per_refresh"])
            )
            stress_funding_symbol = (
                abs(float(weights[index]))
                * float(cost["forward_stress_adverse_funding_reserve_per_8h_full_gross"])
                * int(cost["funding_intervals_per_refresh"])
            )
            gross = float(gross_contributions[index])
            symbol_contributions[symbol] = {
                "side": "long" if weights[index] > 0.0 else "short" if weights[index] < 0.0 else "flat_transition",
                "weight": float(weights[index]),
                "start_weight_change": float(weight_change[index]),
                "asset_return": float(asset_return[index]),
                "gross_contribution": gross,
                "base_net_contribution": gross - base_transaction_symbol - base_funding_symbol,
                "stress_net_contribution": gross - stress_transaction_symbol - stress_funding_symbol,
            }
        gross_return = float(gross_contributions.sum())
        record.update(
            {
                "closed": True,
                "gross_return": gross_return,
                "base_net_return": gross_return - base_transaction - base_funding,
                "stress_net_return": gross_return - stress_transaction - stress_funding,
                "symbol_contributions": symbol_contributions,
                "contribution_month_utc": entry.strftime("%Y-%m"),
            }
        )
    return record


def _write_refresh_snapshots(
    records: list[dict[str, Any]],
    *,
    protocol_hash: str,
    code_hashes: dict[str, str],
) -> dict[str, Any]:
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    verified = 0
    for record in records:
        key = pd.Timestamp(record["detected_and_entry_utc"]).strftime("%Y%m%dT%H%M%SZ")
        json_path = EVIDENCE_DIR / f"refresh_{key}.json"
        sha_path = EVIDENCE_DIR / f"refresh_{key}.sha256"
        payload = {
            "schema": "momentum_fixed_3d_refresh_snapshot_v1",
            "protocol_sha256": protocol_hash,
            "code_sha256": code_hashes,
            "record": record,
        }
        digest = _sha256_bytes(_canonical_json_bytes(payload))
        if json_path.exists() or sha_path.exists():
            if not json_path.exists() or not sha_path.exists():
                raise RuntimeError(f"FAIL_CLOSED: partial snapshot set for {key}")
            existing = _read_json(json_path)
            existing_digest = sha_path.read_text(encoding="utf-8").strip().split()[0]
            actual = _sha256_bytes(_canonical_json_bytes(existing))
            if existing_digest != actual:
                raise RuntimeError(f"FAIL_CLOSED: snapshot hash mismatch for {key}")
        else:
            _atomic_write_json(json_path, payload)
            _atomic_write_text(sha_path, f"{digest}  {json_path.name}\n")
        verified += 1
    return {"status": "PASS", "verified_snapshot_count": verified}


def _current_reference(
    signals: pd.DataFrame,
    weights: np.ndarray,
    refresh_flags: np.ndarray,
    latest_closed: pd.Timestamp,
    symbols: list[str],
) -> dict[str, Any] | None:
    indices = [
        index
        for index, flag in enumerate(refresh_flags)
        if flag and pd.Timestamp(signals.iloc[index]["entry_utc"]) <= latest_closed
    ]
    if not indices:
        return None
    index = indices[-1]
    target = weights[index]
    signal = signals.iloc[index]
    return {
        "label": "REFERENCE_ONLY_NOT_PROSPECTIVE_IF_ENTRY_PRECEDES_REGISTRATION",
        "entry_utc": pd.Timestamp(signal["entry_utc"]).isoformat(),
        "longs": [symbols[i] for i, value in enumerate(target) if value > 0.0],
        "shorts": [symbols[i] for i, value in enumerate(target) if value < 0.0],
        "weights": {
            symbols[i]: float(value)
            for i, value in enumerate(target)
            if float(value) != 0.0
        },
    }


def _selected_data_quality(full_quality: dict[str, Any], symbols: list[str]) -> dict[str, Any]:
    selected = [
        item
        for item in list(full_quality.get("per_symbol") or [])
        if str(item.get("symbol")) in set(symbols)
    ]
    latest_values = {
        str(item.get("latest_closed_15m_utc"))
        for item in selected
        if item.get("latest_closed_15m_utc")
    }
    passed = len(selected) == len(symbols) and all(bool(item.get("pass")) for item in selected) and len(latest_values) == 1
    return {
        "schema": "momentum_fixed_3d_forward_data_quality_v1",
        "checked_at_utc": full_quality.get("checked_at_utc"),
        "window_start_utc": full_quality.get("window_start_utc"),
        "window_end_utc": full_quality.get("window_end_utc"),
        "symbol_count": len(selected),
        "expected_symbol_count": len(symbols),
        "all_symbols_latest_timestamp_aligned": len(latest_values) == 1,
        "total_missing_15m_count": int(sum(item.get("missing_15m_count") or 0 for item in selected)),
        "total_duplicate_closed_timestamp_count": int(
            sum(item.get("duplicate_closed_timestamp_count") or 0 for item in selected)
        ),
        "total_conflicting_duplicate_timestamp_count": int(
            sum(item.get("conflicting_duplicate_timestamp_count") or 0 for item in selected)
        ),
        "total_unclosed_rows_observed_and_excluded": int(
            sum(item.get("unclosed_row_count_observed") or 0 for item in selected)
        ),
        "pass": passed,
        "status": "PASS" if passed else "FAIL_CLOSED",
        "per_symbol": selected,
    }


def build_ledger(prior_ledger: dict[str, Any] | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    protocol = _read_json(PROTOCOL_PATH)
    registration = pd.Timestamp(protocol["registered_at_utc"])
    symbols = [str(symbol) for symbol in protocol["signal"]["symbols"]]
    panels = common.load_panels()
    signals = _daily_signal_frame(
        panels,
        symbols,
        formation_bars_4h=int(protocol["signal"]["formation_bars_4h"]),
    ).reset_index(drop=True)
    decision_times = [pd.Timestamp(value) for value in signals["entry_utc"]]
    signal_protocol = protocol["signal"]
    execution_mode = str(signal_protocol.get("execution_mode") or "fixed_cadence")
    if execution_mode == "fixed_cadence":
        built = fixed_cadence_hysteresis_weights(
            list(signals["scores"]),
            decision_times,
            symbols,
            anchor_utc=signal_protocol["calendar_anchor_utc"],
            cadence_days=int(signal_protocol["refresh_interval_hours"]) // 24,
            top_n=int(signal_protocol["entry_rank"]),
            exit_rank=int(signal_protocol["exit_rank"]),
            gross_exposure=float(signal_protocol["operational_gross_exposure"]),
        )
        observation_cadence_days = int(signal_protocol["refresh_interval_hours"]) // 24
    elif execution_mode == "staggered_equal_cohorts":
        built = staggered_cadence_hysteresis_weights(
            list(signals["scores"]),
            decision_times,
            symbols,
            base_anchor_utc=signal_protocol["calendar_anchor_utc"],
            cohort_offsets_days=tuple(signal_protocol["cohort_offsets_days"]),
            cadence_days=int(signal_protocol["cohort_refresh_interval_hours"]) // 24,
            top_n=int(signal_protocol["entry_rank"]),
            exit_rank=int(signal_protocol["exit_rank"]),
            gross_exposure=float(signal_protocol["operational_gross_exposure"]),
        )
        observation_cadence_days = int(signal_protocol["refresh_interval_hours"]) // 24
    else:
        raise ValueError(f"unsupported execution mode: {execution_mode}")
    latest_closed = pd.Timestamp(panels.m15_close.index[-1])
    eligible = [
        index
        for index, flag in enumerate(built.refresh_flags)
        if flag
        and pd.Timestamp(signals.iloc[index]["entry_utc"]) >= registration
        and pd.Timestamp(signals.iloc[index]["entry_utc"]) <= latest_closed
    ]

    prior_records = list(((prior_ledger or {}).get(VARIANT) or {}).get("observations") or [])
    prior_by_entry = {str(item.get("detected_and_entry_utc")): item for item in prior_records}
    previous_count = len(prior_records)
    recovery_run = bool(previous_count and len(eligible) - previous_count > 1)
    records: list[dict[str, Any]] = []
    previous_weights = np.zeros(len(symbols), dtype=float)
    previous_entry: pd.Timestamp | None = None
    for signal_index in eligible:
        signal = signals.iloc[signal_index]
        entry = pd.Timestamp(signal["entry_utc"])
        target = np.asarray(built.weights[signal_index], dtype=float)
        record = _record_for_refresh(
            signal=signal,
            signal_index=signal_index,
            weights=target,
            previous_weights=previous_weights,
            previous_entry=previous_entry,
            panels=panels,
            protocol=protocol,
            prior=prior_by_entry.get(entry.isoformat()),
            recovery_run=recovery_run,
            symbols=symbols,
        )
        records.append(record)
        previous_weights = target
        previous_entry = entry

    protocol_hash = _sha256_file(PROTOCOL_PATH)
    code_paths = [
        PROJECT_ROOT / "src" / "okx_signal_system" / "research" / "fixed_cadence_momentum.py",
        Path(__file__).resolve(),
        *ADDITIONAL_CODE_PATHS,
    ]
    code_hashes = {str(path.relative_to(PROJECT_ROOT)): _sha256_file(path) for path in code_paths}
    snapshot_integrity = _write_refresh_snapshots(
        records,
        protocol_hash=protocol_hash,
        code_hashes=code_hashes,
    )
    data_quality = _selected_data_quality(
        parent_observations.build_data_quality(latest_closed),
        symbols,
    )
    if data_quality.get("pass") is not True:
        raise RuntimeError("FAIL_CLOSED: prospective 15-minute data quality check failed")

    next_refresh = next_refresh_at_or_after(
        max(registration, latest_closed + pd.Timedelta(minutes=15)),
        anchor_utc=protocol["signal"]["calendar_anchor_utc"],
        cadence_days=observation_cadence_days,
    )
    ledger = {
        "schema": str(protocol.get("forward_ledger_schema") or "momentum_fixed_3d_forward_ledger_v1"),
        "protocol_id": protocol["protocol_id"],
        "registration_at_utc": registration.isoformat(),
        "generated_at_utc": _now_text(),
        "generated_from_closed_data_through_utc": latest_closed.isoformat(),
        "fully_prospective_rebalance_count": int(len(records)),
        "data_quality": data_quality,
        "snapshot_integrity": snapshot_integrity,
        "protocol_sha256": protocol_hash,
        "code_sha256": code_hashes,
        "current_reference_position": _current_reference(
            signals,
            built.weights,
            built.refresh_flags,
            latest_closed,
            symbols,
        ),
        VARIANT: {
            "observations": records,
            "closed_count": int(sum(bool(item.get("closed")) for item in records)),
            "active_count": int(sum(not bool(item.get("closed")) for item in records)),
        },
        "next_expected_refresh_and_entry_utc": next_refresh.isoformat(),
        "run_recovery_backfill": recovery_run,
        "production_effect": "NONE",
        "formal_signal_effect": "NONE",
        "automatic_ordering": False,
    }
    return ledger, protocol


def build_status(
    ledger: dict[str, Any],
    protocol: dict[str, Any],
    *,
    ledger_sha256: str,
) -> dict[str, Any]:
    records = list((ledger.get(VARIANT) or {}).get("observations") or [])
    base = _performance(records, "base_net_return")
    stress = _performance(records, "stress_net_return")
    concentration = _concentration(records)
    registration = pd.Timestamp(protocol["registered_at_utc"])
    closed_through = pd.Timestamp(ledger["generated_from_closed_data_through_utc"])
    elapsed_days = max(0, int((closed_through - registration) / pd.Timedelta(days=1)))
    acceptance = protocol["forward_acceptance"]
    closed_count = int(base["closed_count"])
    minimum_due = (
        elapsed_days >= int(acceptance["minimum_calendar_days"])
        and closed_count >= int(acceptance["minimum_closed_refreshes"])
    )
    preferred_due = (
        elapsed_days >= int(acceptance["preferred_calendar_days"])
        and closed_count >= int(acceptance["preferred_closed_refreshes"])
    )

    def finite(value: Any) -> float | None:
        try:
            result = float(value)
        except (TypeError, ValueError):
            return None
        return result if math.isfinite(result) else None

    checks = {
        "base_profit_factor": finite(base["profit_factor"]) is not None
        and float(base["profit_factor"]) >= float(acceptance["base_profit_factor_min"]),
        "stress_profit_factor": finite(stress["profit_factor"]) is not None
        and float(stress["profit_factor"]) >= float(acceptance["stress_profit_factor_min"]),
        "base_total_return_positive": finite(base["total_return"]) is not None
        and float(base["total_return"]) > 0.0,
        "stress_total_return_positive": finite(stress["total_return"]) is not None
        and float(stress["total_return"]) > 0.0,
        "base_drawdown_within_limit": finite(base["maximum_drawdown"]) is not None
        and abs(float(base["maximum_drawdown"])) <= float(acceptance["base_maximum_drawdown_abs_max"]),
        "stress_drawdown_within_limit": finite(stress["maximum_drawdown"]) is not None
        and abs(float(stress["maximum_drawdown"])) <= float(acceptance["stress_maximum_drawdown_abs_max"]),
        "single_symbol_concentration_within_limit": finite(
            concentration["single_symbol_positive_contribution_share"]
        )
        is not None
        and float(concentration["single_symbol_positive_contribution_share"])
        <= float(acceptance["single_symbol_positive_contribution_share_max"]),
        "single_month_concentration_within_limit": finite(concentration["single_positive_month_share"])
        is not None
        and float(concentration["single_positive_month_share"])
        <= float(acceptance["single_positive_month_share_max"]),
        "top5_refresh_concentration_within_limit": finite(
            concentration["top_5_profitable_refreshes_positive_contribution_share"]
        )
        is not None
        and float(concentration["top_5_profitable_refreshes_positive_contribution_share"])
        <= float(acceptance["top_5_profitable_refreshes_positive_contribution_share_max"]),
    }
    checks_pass = bool(all(checks.values()))
    if not minimum_due:
        decision = str(acceptance["decision_before_due"])
        fixed_status = "NOT_EVALUATED_SAMPLE_INCOMPLETE"
        fixed_all_pass: bool | None = None
    elif preferred_due and checks_pass:
        decision = str(acceptance["decision_preferred_pass"])
        fixed_status = "PASS"
        fixed_all_pass = True
    elif preferred_due:
        decision = str(acceptance["decision_fail"])
        fixed_status = "FAIL"
        fixed_all_pass = False
    elif checks_pass:
        decision = str(acceptance["decision_pass"])
        fixed_status = "PASS"
        fixed_all_pass = True
    else:
        decision = str(acceptance["decision_minimum_not_passed"])
        fixed_status = "FAIL"
        fixed_all_pass = False

    return {
        "schema": str(protocol.get("forward_status_schema") or "momentum_fixed_3d_forward_status_v1"),
        "acceptance_protocol_id": protocol["protocol_id"],
        "candidate_protocol_id": protocol["protocol_id"],
        "generated_at_utc": _now_text(),
        "registration_at_utc": protocol["registered_at_utc"],
        "closed_data_through_utc": ledger["generated_from_closed_data_through_utc"],
        "elapsed_closed_data_days": elapsed_days,
        "fully_prospective_rebalance_count": int(ledger["fully_prospective_rebalance_count"]),
        "minimum_closed_refreshes": int(acceptance["minimum_closed_refreshes"]),
        "minimum_calendar_days": int(acceptance["minimum_calendar_days"]),
        "preferred_closed_refreshes": int(acceptance["preferred_closed_refreshes"]),
        "preferred_calendar_days": int(acceptance["preferred_calendar_days"]),
        "next_expected_refresh_and_entry_utc": ledger["next_expected_refresh_and_entry_utc"],
        "decision": decision,
        "signal_level": "研究级/影子信号",
        "formal_a": False,
        "automatic_promotion": False,
        "automatic_ordering": False,
        "protocol_integrity": "PASS",
        "ledger_integrity": "PASS",
        "data_quality_integrity": "PASS",
        "daily_snapshot_chain_integrity": "PASS",
        "protocol_sha256": ledger["protocol_sha256"],
        "ledger_sha256": ledger_sha256,
        "variants": {
            VARIANT: {
                "closed_count": int(base["closed_count"]),
                "active_count": int((ledger.get(VARIANT) or {}).get("active_count") or 0),
                "base": base,
                "stress": stress,
                **concentration,
            }
        },
        "fixed_gate_results": {
            VARIANT: {
                "status": fixed_status,
                "all_pass": fixed_all_pass,
                "due": minimum_due,
                "minimum_due": minimum_due,
                "preferred_due": preferred_due,
                "checks": checks,
            }
        },
        "variant_fixed_gate_results": {
            VARIANT: {
                "status": fixed_status,
                "all_pass": fixed_all_pass,
                "due": minimum_due,
                "minimum_due": minimum_due,
                "preferred_due": preferred_due,
                "checks": checks,
            }
        },
        "current_reference_position": ledger.get("current_reference_position"),
        "historical_warning": str(
            protocol["historical_evidence"].get("random_time_warning")
            or protocol["historical_evidence"].get("caution")
            or ""
        ),
        "production_effect": "NONE",
        "formal_signal_effect": "NONE",
    }


def run() -> dict[str, Any]:
    prior = _load_optional_json(LEDGER_PATH)
    ledger, protocol = build_ledger(prior)
    _atomic_write_json(LEDGER_PATH, ledger)
    ledger_hash = _sha256_file(LEDGER_PATH)
    status = build_status(ledger, protocol, ledger_sha256=ledger_hash)
    _atomic_write_json(STATUS_PATH, status)
    return status


def main() -> int:
    status = run()
    print(json.dumps(status, ensure_ascii=False, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
