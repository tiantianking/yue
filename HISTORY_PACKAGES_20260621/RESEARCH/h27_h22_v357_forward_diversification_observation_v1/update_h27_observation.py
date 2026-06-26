from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

WORKSPACE = Path(__file__).resolve().parents[3]
PROJECT = WORKSPACE / "1_CODE_代码" / "okx-contract-signal-system"
PROTOCOL_PATH = Path(__file__).with_name("PROTOCOL_FROZEN.json")
STATUS_PATH = Path(__file__).with_name("STATUS.json")
H22_LEDGER = PROJECT / "outputs" / "momentum_staggered_3x3_forward_ledger.json"
H22_STATUS = PROJECT / "outputs" / "momentum_staggered_3x3_forward_status.json"
V357_LEDGER = PROJECT / "outputs" / "shadow_ensemble_forward_ledger.json"
V357_STATUS = PROJECT / "outputs" / "shadow_ensemble_forward_acceptance_status.json"
H22_VARIANT = "staggered_3x3_refresh_hysteresis_4_in_6_out"
V357_VARIANTS = ("DC_n24_t50_slow", "VCB_A")


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON object required: {path}")
    return payload


def now_text() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def day_key(value: str | pd.Timestamp) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    timestamp = timestamp.tz_localize("UTC") if timestamp.tzinfo is None else timestamp.tz_convert("UTC")
    return (timestamp - pd.Timedelta(hours=4)).floor("D") + pd.Timedelta(hours=4)


def finite_correlation(left: pd.Series, right: pd.Series, *, method: str) -> float | None:
    joined = pd.concat([left.rename("left"), right.rename("right")], axis=1).dropna()
    if len(joined) < 10 or joined["left"].nunique() < 2 or joined["right"].nunique() < 2:
        return None
    value = joined["left"].corr(joined["right"], method=method)
    return float(value) if pd.notna(value) else None


def h22_daily(ledger: dict[str, Any], field: str) -> pd.Series:
    observations = list(((ledger.get(H22_VARIANT) or {}).get("observations") or []))
    values: dict[pd.Timestamp, float] = {}
    for item in observations:
        if not bool(item.get("closed")) or item.get(field) is None:
            continue
        timestamp = item.get("exit_utc") or item.get("detected_and_entry_utc")
        if not timestamp:
            continue
        key = day_key(str(timestamp))
        values[key] = values.get(key, 0.0) + float(item[field])
    return pd.Series(values, dtype=float).sort_index()


def v357_daily(ledger: dict[str, Any], *, stress: bool) -> tuple[pd.Series, int]:
    values: dict[pd.Timestamp, float] = {}
    closed_count = 0
    for variant in V357_VARIANTS:
        observations = list(((ledger.get(variant) or {}).get("observations") or []))
        for item in observations:
            if bool(item.get("is_warmup")):
                continue
            if item.get("base_net_r") is None or item.get("gross_r") is None or not item.get("exit_time_utc"):
                continue
            closed_count += 1
            gross = float(item["gross_r"])
            base = float(item["base_net_r"])
            normalized = gross - 2.0 * (gross - base) if stress else base
            key = day_key(str(item["exit_time_utc"]))
            values[key] = values.get(key, 0.0) + 0.005 * normalized
    return pd.Series(values, dtype=float).sort_index(), closed_count


def shared_loss_fraction(left: pd.Series, right: pd.Series) -> tuple[float | None, float | None, float | None]:
    both = (left < 0.0) & (right < 0.0)
    left_losses = int((left < 0.0).sum())
    right_losses = int((right < 0.0).sum())
    days = len(left)
    return (
        float(both.mean()) if days else None,
        float(both.sum() / left_losses) if left_losses else None,
        float(both.sum() / right_losses) if right_losses else None,
    )


def top_five_joint_loss_share(left: pd.Series, right: pd.Series) -> float | None:
    joint = (left + right).loc[(left < 0.0) & (right < 0.0)]
    losses = -joint.loc[joint < 0.0].sort_values()
    total = float(losses.sum())
    return float(losses.head(5).sum() / total) if total > 0.0 else None


def rolling_worst(values: pd.Series, window: int) -> float | None:
    if len(values) < window:
        return None
    return float(values.rolling(window).sum().min())


def build_status() -> dict[str, Any]:
    protocol = read_json(PROTOCOL_PATH)
    h22_ledger = read_json(H22_LEDGER)
    h22_status = read_json(H22_STATUS)
    v357_ledger = read_json(V357_LEDGER)
    v357_status = read_json(V357_STATUS)
    registration = pd.Timestamp("2026-06-26T16:12:35Z")

    h22_base = h22_daily(h22_ledger, "base_net_return")
    h22_stress = h22_daily(h22_ledger, "stress_net_return")
    v357_base, v357_closed = v357_daily(v357_ledger, stress=False)
    v357_stress, _ = v357_daily(v357_ledger, stress=True)

    closed_candidates = [
        pd.Timestamp(h22_ledger.get("generated_from_closed_data_through_utc")),
        pd.Timestamp(v357_ledger.get("generated_from_closed_data_through_utc")),
    ]
    closed_through = min(closed_candidates)
    first_day = day_key(registration)
    last_day = day_key(closed_through)
    index = pd.date_range(first_day, last_day, freq="1D", tz="UTC") if last_day >= first_day else pd.DatetimeIndex([], tz="UTC")
    h22_base = h22_base.reindex(index, fill_value=0.0)
    h22_stress = h22_stress.reindex(index, fill_value=0.0)
    v357_base = v357_base.reindex(index, fill_value=0.0)
    v357_stress = v357_stress.reindex(index, fill_value=0.0)

    base_pearson = finite_correlation(h22_base, v357_base, method="pearson")
    base_spearman = finite_correlation(h22_base, v357_base, method="spearman")
    stress_pearson = finite_correlation(h22_stress, v357_stress, method="pearson")
    stress_spearman = finite_correlation(h22_stress, v357_stress, method="spearman")
    both_loss, h22_shared, v357_shared = shared_loss_fraction(h22_base, v357_base)
    joint_base = h22_base + v357_base

    h22_closed = int(((h22_ledger.get(H22_VARIANT) or {}).get("closed_count") or 0))
    elapsed_days = max(0, int((closed_through - registration) / pd.Timedelta(days=1)))
    minimum = protocol["minimum_evidence"]
    minimum_due = (
        elapsed_days >= int(minimum["calendar_days"])
        and h22_closed >= int(minimum["h22_closed_observations"])
        and v357_closed >= int(minimum["v357_closed_trades"])
    )
    preferred_due = (
        elapsed_days >= int(minimum["preferred_calendar_days"])
        and h22_closed >= int(minimum["preferred_h22_closed_observations"])
        and v357_closed >= int(minimum["preferred_v357_closed_trades"])
    )
    quality_pass = bool(
        h22_ledger.get("data_quality", {}).get("pass") is True
        and h22_status.get("ledger_integrity") == "PASS"
        and h22_status.get("daily_snapshot_chain_integrity") == "PASS"
        and v357_status.get("ledger_integrity") == "PASS"
        and v357_status.get("daily_snapshot_chain_integrity") == "PASS"
    )
    conditions = protocol["review_conditions"]
    checks = {
        "data_quality": quality_pass,
        "base_correlation": base_pearson is not None
        and abs(base_pearson) <= float(conditions["maximum_absolute_base_daily_correlation"]),
        "stress_correlation": stress_pearson is not None
        and abs(stress_pearson) <= float(conditions["maximum_absolute_stress_daily_correlation"]),
        "h22_shared_loss_fraction": h22_shared is not None
        and h22_shared <= float(conditions["maximum_shared_loss_day_fraction_of_either_component"]),
        "v357_shared_loss_fraction": v357_shared is not None
        and v357_shared <= float(conditions["maximum_shared_loss_day_fraction_of_either_component"]),
        "top_five_joint_loss_share": top_five_joint_loss_share(h22_base, v357_base) is not None
        and float(top_five_joint_loss_share(h22_base, v357_base) or 0.0)
        <= float(conditions["maximum_top_five_joint_loss_share"]),
    }
    if not minimum_due:
        decision = "RECORD_ONLY_SAMPLE_INCOMPLETE"
        all_pass: bool | None = None
    else:
        all_pass = bool(all(checks.values()))
        decision = (
            protocol["decision_scope"]["pass"]
            if all_pass
            else protocol["decision_scope"]["fail"]
        )
    return {
        "schema": "okx_forward_diversification_observation_status_v1",
        "protocol_id": protocol["protocol_id"],
        "generated_at_utc": now_text(),
        "closed_data_through_utc": closed_through.isoformat(),
        "elapsed_calendar_days": elapsed_days,
        "h22_closed_observations": h22_closed,
        "v357_closed_trades": v357_closed,
        "minimum_due": minimum_due,
        "preferred_due": preferred_due,
        "decision": decision,
        "all_pass": all_pass,
        "statistics": {
            "common_daily_rows": int(len(index)),
            "base_pearson": base_pearson,
            "base_spearman": base_spearman,
            "stress_pearson": stress_pearson,
            "stress_spearman": stress_spearman,
            "both_loss_day_fraction": both_loss,
            "h22_loss_days_shared_by_v357": h22_shared,
            "v357_loss_days_shared_by_h22": v357_shared,
            "top_five_joint_loss_share": top_five_joint_loss_share(h22_base, v357_base),
            "worst_common_10_day_base_return": rolling_worst(joint_base, 10),
            "worst_common_20_day_base_return": rolling_worst(joint_base, 20),
        },
        "checks": checks,
        "production_effect": "NONE",
        "formal_signal_effect": "NONE",
        "weight_selection_allowed": False,
        "automatic_ordering": False,
        "automatic_promotion": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    status = build_status()
    text = json.dumps(status, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if not args.dry_run:
        STATUS_PATH.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
