from __future__ import annotations

"""Adapt the frozen v3.57 shadow ensemble into parallel forward acceptance.

Only non-warmup observations are admitted. The adapter converts the existing
R-multiple ledger into a fixed risk-normalized evidence view for governance; it
never changes the underlying candidate, runtime, sizing, or production signals.
"""

import hashlib
import json
import math
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from okx_signal_system.config import project_paths
from okx_signal_system.io_atomic import write_text_atomic

FROZEN_PROTOCOL_SHA256 = "170544635a97f50ab52f4f9af367b657b9c2d5db3dbd4f335319b0a82e1ca526"
MEMBERS = ("DC_n24_t50_slow", "VCB_A")


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON object required: {path}")
    return payload


def _canonical_json_sha256(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _canonical_json_file_sha256(path: Path) -> str:
    return _canonical_json_sha256(_read_json(path))


def load_frozen_protocol(path: Path | None = None) -> tuple[dict[str, Any], Path, str]:
    paths = project_paths()
    protocol_path = path or paths.config_dir / "shadow_ensemble_forward_acceptance_protocol.json"
    payload = _read_json(protocol_path)
    digest = _canonical_json_sha256(payload)
    if digest != FROZEN_PROTOCOL_SHA256:
        raise ValueError(f"shadow ensemble acceptance protocol checksum invalid: {digest}")
    if payload.get("schema") != "okx_shadow_ensemble_forward_acceptance_protocol_v1":
        raise ValueError("shadow ensemble acceptance protocol schema invalid")
    if payload.get("research_only") is not True or payload.get("production_effect") != "NONE":
        raise ValueError("shadow ensemble acceptance protocol boundary invalid")
    for member, spec in payload.get("candidate_files", {}).items():
        candidate_path = paths.root / str(spec["path"])
        if _canonical_json_file_sha256(candidate_path) != str(spec["sha256"]):
            raise ValueError(f"frozen shadow candidate checksum invalid: {member}")
    return payload, protocol_path, digest


def read_observations(sqlite_path: Path) -> list[dict[str, Any]]:
    if not sqlite_path.is_file():
        raise FileNotFoundError(sqlite_path)
    connection = sqlite3.connect(sqlite_path)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """
            SELECT *
            FROM shadow_observations
            WHERE is_warmup = 0
            ORDER BY signal_time, observation_id
            """
        ).fetchall()
    finally:
        connection.close()
    return [dict(row) for row in rows]


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _profit_factor(values: Iterable[float]) -> tuple[float | None, bool]:
    numbers = [float(value) for value in values if math.isfinite(float(value))]
    gains = sum(value for value in numbers if value > 0)
    losses = -sum(value for value in numbers if value < 0)
    if losses > 0:
        return gains / losses, False
    if gains > 0:
        return None, True
    return 0.0, False


def _equity_metrics(values: list[float], risk_fraction: float) -> tuple[float, float]:
    equity = 1.0
    peak = 1.0
    maximum_drawdown = 0.0
    for value in values:
        step = max(-0.999999, float(value) * risk_fraction)
        equity *= 1.0 + step
        peak = max(peak, equity)
        maximum_drawdown = min(maximum_drawdown, equity / peak - 1.0)
    return equity - 1.0, maximum_drawdown


def _positive_share(rows: list[dict[str, Any]], key: str) -> float:
    contributions: dict[str, float] = defaultdict(float)
    for row in rows:
        value = float(row["base_r"])
        if value > 0:
            contributions[str(row[key])] += value
    total = sum(contributions.values())
    return max(contributions.values(), default=0.0) / total if total > 0 else 0.0


def _top_positive_share(rows: list[dict[str, Any]], count: int = 5) -> float:
    positives = sorted((float(row["base_r"]) for row in rows if float(row["base_r"]) > 0), reverse=True)
    total = sum(positives)
    return sum(positives[:count]) / total if total > 0 else 0.0


def _month(value: str | None) -> str:
    if not value:
        return "UNKNOWN"
    return str(value)[:7]


def _snapshot_hash(entry: Mapping[str, Any]) -> str:
    material = {
        "snapshot_key": entry.get("snapshot_key"),
        "closed_data_through_utc": entry.get("closed_data_through_utc"),
        "source_status_sha256": entry.get("source_status_sha256"),
        "source_sqlite_sha256": entry.get("source_sqlite_sha256"),
        "ledger_sha256": entry.get("ledger_sha256"),
        "previous_entry_sha256": entry.get("previous_entry_sha256"),
    }
    if "source_evidence_sha256" in entry:
        material["source_evidence_sha256"] = entry.get("source_evidence_sha256")
    encoded = json.dumps(
        material,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def update_snapshot_chain(
    path: Path,
    *,
    closed_data_through_utc: str,
    source_status_sha256: str,
    source_sqlite_sha256: str,
    evidence_digest: str,
    ledger_sha256: str,
) -> dict[str, Any]:
    chain = _read_json(path) if path.is_file() else {
        "schema": "okx_shadow_ensemble_forward_snapshot_chain_v1",
        "snapshots": [],
    }
    snapshots = chain.get("snapshots")
    if not isinstance(snapshots, list):
        raise ValueError("shadow ensemble snapshot chain snapshots must be a list")
    previous: str | None = None
    for entry in snapshots:
        if not isinstance(entry, Mapping):
            raise ValueError("shadow ensemble snapshot chain entry invalid")
        if entry.get("previous_entry_sha256") != previous:
            raise ValueError("shadow ensemble snapshot chain previous hash invalid")
        expected = _snapshot_hash(entry)
        if entry.get("entry_sha256") != expected:
            raise ValueError("shadow ensemble snapshot chain entry hash invalid")
        previous = expected
    snapshot_key = f"{closed_data_through_utc}|{evidence_digest[:16]}|{ledger_sha256[:16]}"
    if not snapshots or snapshots[-1].get("snapshot_key") != snapshot_key:
        entry: dict[str, Any] = {
            "snapshot_key": snapshot_key,
            "closed_data_through_utc": closed_data_through_utc,
            "source_status_sha256": source_status_sha256,
            "source_sqlite_sha256": source_sqlite_sha256,
            "source_evidence_sha256": evidence_digest,
            "ledger_sha256": ledger_sha256,
            "previous_entry_sha256": previous,
        }
        entry["entry_sha256"] = _snapshot_hash(entry)
        snapshots.append(entry)
    chain["snapshot_count"] = len(snapshots)
    chain["latest_entry_sha256"] = snapshots[-1]["entry_sha256"] if snapshots else None
    chain["updated_at_utc"] = datetime.now(timezone.utc).isoformat()
    path.parent.mkdir(parents=True, exist_ok=True)
    write_text_atomic(json.dumps(chain, ensure_ascii=False, indent=2), path)
    return chain


def build_variant_summary(
    rows: list[dict[str, Any]],
    *,
    risk_fraction: float,
    stress_cost_multiplier: float,
) -> dict[str, Any]:
    closed_rows: list[dict[str, Any]] = []
    for row in rows:
        gross = _finite(row.get("gross_r"))
        base = _finite(row.get("estimated_net_r"))
        if gross is None or base is None or not row.get("exit_time"):
            continue
        cost = max(0.0, gross - base)
        stress = gross - stress_cost_multiplier * cost
        closed_rows.append(
            {
                **row,
                "base_r": base,
                "stress_r": stress,
                "month": _month(str(row.get("exit_time"))),
            }
        )
    closed_rows.sort(key=lambda row: (str(row.get("exit_time")), str(row.get("observation_id"))))
    base_values = [float(row["base_r"]) for row in closed_rows]
    stress_values = [float(row["stress_r"]) for row in closed_rows]
    base_pf, base_infinite = _profit_factor(base_values)
    stress_pf, stress_infinite = _profit_factor(stress_values)
    base_return, base_drawdown = _equity_metrics(base_values, risk_fraction)
    stress_return, stress_drawdown = _equity_metrics(stress_values, risk_fraction)
    month_totals: dict[str, float] = defaultdict(float)
    for row in closed_rows:
        month_totals[str(row["month"])] += float(row["base_r"])
    return {
        "observation_count": len(rows),
        "closed_count": len(closed_rows),
        "active_count": sum(1 for row in rows if str(row.get("state")) in {"ACTIVE", "PENDING_ENTRY"}),
        "base": {
            "profit_factor": base_pf,
            "profit_factor_infinite": base_infinite,
            "total_return": base_return,
            "maximum_drawdown": base_drawdown,
            "net_r_sum": sum(base_values),
        },
        "stress": {
            "profit_factor": stress_pf,
            "profit_factor_infinite": stress_infinite,
            "total_return": stress_return,
            "maximum_drawdown": stress_drawdown,
            "net_r_sum": sum(stress_values),
        },
        "single_symbol_positive_contribution_share": _positive_share(closed_rows, "symbol"),
        "single_month_positive_contribution_share": _positive_share(closed_rows, "month"),
        "top_5_profitable_rebalances_positive_contribution_share": _top_positive_share(closed_rows),
        "positive_month_count": sum(1 for value in month_totals.values() if value > 0),
        "closed_rows": closed_rows,
    }


def _pf_value(block: Mapping[str, Any]) -> float:
    if block.get("profit_factor_infinite") is True:
        return float("inf")
    value = _finite(block.get("profit_factor"))
    return value if value is not None else 0.0


def fixed_gate_result(summary: Mapping[str, Any], protocol: Mapping[str, Any], *, sample_due: bool) -> dict[str, Any]:
    if not sample_due:
        return {
            "status": "NOT_EVALUATED_SAMPLE_INCOMPLETE",
            "all_pass": None,
            "checks": {},
        }
    gates = protocol["minimum_acceptance"]
    base = summary["base"]
    stress = summary["stress"]
    checks = {
        "base_profit_factor_at_least_1_10": _pf_value(base) >= float(gates["base_profit_factor_min"]),
        "stress_profit_factor_at_least_1_03": _pf_value(stress) >= float(gates["stress_profit_factor_min"]),
        "base_total_return_positive": float(base["total_return"]) > float(gates["base_total_return_min"]),
        "stress_total_return_positive": float(stress["total_return"]) > float(gates["stress_total_return_min"]),
        "base_maximum_drawdown_not_below_minus_15_percent": float(base["maximum_drawdown"]) >= float(gates["base_maximum_drawdown_min"]),
        "stress_maximum_drawdown_not_below_minus_15_percent": float(stress["maximum_drawdown"]) >= float(gates["stress_maximum_drawdown_min"]),
        "single_symbol_positive_contribution_share_not_above_25_percent": float(summary["single_symbol_positive_contribution_share"]) <= float(gates["single_symbol_positive_contribution_share_max"]),
        "single_month_positive_contribution_share_within_sample_cap": float(summary["single_month_positive_contribution_share"]) <= float(gates["single_month_positive_contribution_share_max"]),
        "top_5_profitable_rebalances_share_not_above_50_percent": float(summary["top_5_profitable_rebalances_positive_contribution_share"]) <= float(gates["top_5_positive_contribution_share_max"]),
        "positive_month_count_meets_sample_minimum": int(summary["positive_month_count"]) >= int(gates["minimum_positive_months"]),
    }
    passed = all(checks.values())
    return {
        "status": "PASS" if passed else "FAIL",
        "all_pass": passed,
        "checks": checks,
    }


def _ledger_observation(row: Mapping[str, Any]) -> dict[str, Any]:
    side = str(row.get("side"))
    symbol = str(row.get("symbol"))
    return {
        "observation_id": row.get("observation_id"),
        "detected_and_entry_utc": row.get("entry_time") or row.get("signal_time"),
        "signal_time_utc": row.get("signal_time"),
        "longs": [symbol] if side == "long" else [],
        "shorts": [symbol] if side == "short" else [],
        "state": row.get("state"),
        "exit_time_utc": row.get("exit_time"),
        "outcome": row.get("outcome"),
        "bars_held": row.get("bars_held"),
        "gross_r": row.get("gross_r"),
        "base_net_r": row.get("estimated_net_r"),
        "is_warmup": False,
    }


def build_acceptance_payloads(
    source_status: Mapping[str, Any],
    observations: list[dict[str, Any]],
    protocol: Mapping[str, Any],
    *,
    protocol_sha256: str,
    sqlite_sha256: str,
    source_status_sha256: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    latest = source_status.get("latest_closed_4h")
    latest_ts = datetime.fromisoformat(str(latest).replace("Z", "+00:00")) if latest else datetime.now(timezone.utc)
    signal_times = [
        datetime.fromisoformat(str(row["signal_time"]).replace("Z", "+00:00"))
        for row in observations
        if row.get("signal_time")
    ]
    first_ts = min(signal_times) if signal_times else latest_ts
    elapsed_days = max(0, (latest_ts.date() - first_ts.date()).days + 1)
    rules = protocol["evidence_rules"]
    variants: dict[str, Any] = {}
    ledger_variants: dict[str, Any] = {}
    gate_results: dict[str, Any] = {}
    minimum = protocol["minimum_acceptance"]
    prospective_count = len(observations)
    for member in MEMBERS:
        member_rows = [row for row in observations if str(row.get("member")) == member]
        summary = build_variant_summary(
            member_rows,
            risk_fraction=float(rules["portfolio_risk_fraction_per_closed_observation"]),
            stress_cost_multiplier=float(rules["stress_cost_multiplier"]),
        )
        closed_rows = summary.pop("closed_rows")
        variants[member] = summary
        member_sample_due = (
            elapsed_days >= int(minimum["elapsed_closed_data_days"])
            and int(summary["observation_count"]) >= int(minimum["fully_prospective_observations"])
        )
        gate_results[member] = fixed_gate_result(summary, protocol, sample_due=member_sample_due)
        ledger_variants[member] = {
            "observations": [_ledger_observation(row) for row in member_rows],
            "closed_count": len(closed_rows),
            "active_count": summary["active_count"],
        }
    evidence_digest = _canonical_json_sha256(ledger_variants)
    integrity_pass = (
        source_status.get("status") == "running"
        and source_status.get("research_only") is True
        and source_status.get("isolated_from_formal_runtime") is True
        and int(source_status.get("eligible_symbols") or 0) == 21
        and not source_status.get("skipped_symbols")
    )
    status = {
        "schema": "okx_shadow_ensemble_forward_acceptance_status_v1",
        "acceptance_protocol_id": protocol["protocol_id"],
        "candidate_protocol_id": protocol["candidate_id"],
        "protocol_sha256": protocol_sha256,
        "ledger_sha256": None,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "registration_at_utc": first_ts.astimezone(timezone.utc).isoformat(),
        "closed_data_through_utc": latest_ts.astimezone(timezone.utc).isoformat(),
        "elapsed_closed_data_days": elapsed_days,
        "fully_prospective_rebalance_count": prospective_count,
        "protocol_integrity": "PASS",
        "ledger_integrity": "PASS",
        "data_quality_integrity": "PASS" if integrity_pass else "FAIL",
        "daily_snapshot_chain_integrity": "PENDING",
        "data_quality": {
            "eligible_symbols": source_status.get("eligible_symbols"),
            "skipped_symbols": source_status.get("skipped_symbols"),
            "source_status_sha256": source_status_sha256,
            "source_sqlite_sha256": sqlite_sha256,
            "evidence_digest": evidence_digest,
        },
        "sample_verdict": (
            "READY_FOR_FIXED_GATE"
            if all(result.get("all_pass") is not None for result in gate_results.values())
            else "NOT_EVALUATED_SAMPLE_INCOMPLETE"
        ),
        "minimum_sample_gate": all(result.get("all_pass") is not None for result in gate_results.values()),
        "preferred_sample_gate": (
            elapsed_days >= int(protocol["preferred_confirmation"]["elapsed_closed_data_days"])
            and all(
                int(summary["observation_count"])
                >= int(protocol["preferred_confirmation"]["fully_prospective_observations"])
                for summary in variants.values()
            )
        ),
        "variants": variants,
        "variant_fixed_gate_results": gate_results,
        "decision": "MANUAL_REVIEW_ONLY_NO_AUTO_PROMOTION",
        "automatic_promotion": False,
        "production_effect": "NONE",
    }
    ledger = {
        "schema": "okx_shadow_ensemble_forward_ledger_v1",
        "protocol_id": protocol["protocol_id"],
        "registration_at_utc": first_ts.astimezone(timezone.utc).isoformat(),
        "generated_at_utc": latest_ts.astimezone(timezone.utc).isoformat(),
        "generated_from_closed_data_through_utc": latest_ts.astimezone(timezone.utc).isoformat(),
        "fully_prospective_signal_count": prospective_count,
        "evidence_digest": evidence_digest,
        **ledger_variants,
        "production_effect": "NONE",
    }
    return status, ledger


def write_acceptance_outputs(
    *,
    source_status_path: Path | None = None,
    sqlite_path: Path | None = None,
    output_status_path: Path | None = None,
    output_ledger_path: Path | None = None,
    output_chain_path: Path | None = None,
) -> dict[str, Any]:
    paths = project_paths()
    source_status_path = source_status_path or paths.output_dir / "shadow_ensemble_status.json"
    sqlite_path = sqlite_path or paths.output_dir / "shadow_ensemble.sqlite3"
    output_status_path = output_status_path or paths.output_dir / "shadow_ensemble_forward_acceptance_status.json"
    output_ledger_path = output_ledger_path or paths.output_dir / "shadow_ensemble_forward_ledger.json"
    output_chain_path = output_chain_path or paths.output_dir / "shadow_ensemble_forward_snapshot_chain.json"
    protocol, _protocol_path, protocol_sha256 = load_frozen_protocol()
    source_status = _read_json(source_status_path)
    observations = read_observations(sqlite_path)
    source_status_sha256 = _sha256_file(source_status_path)
    sqlite_sha256 = _sha256_file(sqlite_path)
    status, ledger = build_acceptance_payloads(
        source_status,
        observations,
        protocol,
        protocol_sha256=protocol_sha256,
        sqlite_sha256=sqlite_sha256,
        source_status_sha256=source_status_sha256,
    )
    output_ledger_path.parent.mkdir(parents=True, exist_ok=True)
    write_text_atomic(json.dumps(ledger, ensure_ascii=False, indent=2), output_ledger_path)
    ledger_sha256 = _sha256_file(output_ledger_path)
    chain = update_snapshot_chain(
        output_chain_path,
        closed_data_through_utc=str(status["closed_data_through_utc"]),
        source_status_sha256=source_status_sha256,
        source_sqlite_sha256=sqlite_sha256,
        evidence_digest=str(ledger["evidence_digest"]),
        ledger_sha256=ledger_sha256,
    )
    status["ledger_sha256"] = ledger_sha256
    status["daily_snapshot_chain_integrity"] = "PASS"
    status["daily_snapshot_chain"] = {
        "path": str(output_chain_path),
        "snapshot_count": chain["snapshot_count"],
        "latest_entry_sha256": chain["latest_entry_sha256"],
        "chain_sha256": _sha256_file(output_chain_path),
    }
    write_text_atomic(json.dumps(status, ensure_ascii=False, indent=2), output_status_path)
    return {
        "status": "UPDATED",
        "source_status": str(source_status_path),
        "source_sqlite": str(sqlite_path),
        "output_status": str(output_status_path),
        "output_ledger": str(output_ledger_path),
        "output_snapshot_chain": str(output_chain_path),
        "elapsed_closed_data_days": status["elapsed_closed_data_days"],
        "fully_prospective_observations": status["fully_prospective_rebalance_count"],
        "production_effect": "NONE",
    }


__all__ = [
    "FROZEN_PROTOCOL_SHA256",
    "MEMBERS",
    "build_acceptance_payloads",
    "build_variant_summary",
    "fixed_gate_result",
    "load_frozen_protocol",
    "read_observations",
    "update_snapshot_chain",
    "write_acceptance_outputs",
]
