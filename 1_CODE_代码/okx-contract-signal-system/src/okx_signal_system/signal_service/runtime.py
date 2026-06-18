from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from okx_signal_system.config import project_paths
from okx_signal_system.research.approved_strategy_manifest import (
    ApprovedManifestStatus,
    load_approved_manifest_status,
    strategy_params_from_dict,
)
from okx_signal_system.strategy.trend_breakout import StrategyParams


class RuntimeStrategyManifestError(RuntimeError):
    pass


def params_from_dict(data: dict) -> StrategyParams:
    return strategy_params_from_dict(data)


def load_candidate_strategy_params(output_dir: str | Path | None = None) -> StrategyParams:
    out = Path(output_dir) if output_dir else project_paths().output_dir
    path = out / "candidate_params.json"
    if not path.exists():
        return StrategyParams()
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return StrategyParams()
    raw = data.get("candidate_params", data)
    if not isinstance(raw, dict):
        return StrategyParams()
    return params_from_dict(raw)


def load_selected_strategy_params_status(output_dir: str | Path | None = None) -> ApprovedManifestStatus:
    return load_approved_manifest_status(output_dir)


def load_selected_strategy_params(output_dir: str | Path | None = None) -> StrategyParams:
    return load_selected_strategy_params_status(output_dir).params


@dataclass(frozen=True)
class RuntimeStrategyConfig:
    strategy_params: StrategyParams
    approved_manifest_hash: str
    params_hash: str
    push_allowed: bool
    reason: str


def load_runtime_strategy_config(output_dir: str | Path | None = None) -> RuntimeStrategyConfig:
    status = load_selected_strategy_params_status(output_dir)
    manifest_hash = str(status.manifest.get("manifest_hash", "")) if status.manifest else ""
    params_hash = str(status.manifest.get("selected_params_sha256", "")) if status.manifest else ""
    return RuntimeStrategyConfig(
        strategy_params=status.params,
        approved_manifest_hash=manifest_hash,
        params_hash=params_hash,
        push_allowed=status.ok,
        reason=status.reason,
    )


def latest_bar_age_hours(frame: pd.DataFrame, now: pd.Timestamp | None = None) -> float | None:
    if frame.empty or "ts" not in frame.columns:
        return None
    latest = pd.to_datetime(frame["ts"].iloc[-1], utc=True)
    ref = now or pd.Timestamp.now(tz="UTC")
    return float((ref - latest).total_seconds() / 3600)


def is_latest_bar_fresh(
    frame: pd.DataFrame,
    *,
    max_lag_hours: float = 3.0,
    now: pd.Timestamp | None = None,
) -> bool:
    age = latest_bar_age_hours(frame, now)
    return age is not None and age <= max_lag_hours


__all__ = [
    "ApprovedManifestStatus",
    "RuntimeStrategyConfig",
    "RuntimeStrategyManifestError",
    "is_latest_bar_fresh",
    "latest_bar_age_hours",
    "load_candidate_strategy_params",
    "load_selected_strategy_params",
    "load_selected_strategy_params_status",
    "load_runtime_strategy_config",
    "params_from_dict",
]
