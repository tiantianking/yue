from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from okx_signal_system.paths import package_project_root
from okx_signal_system.risk.costs import CostConfig
from okx_signal_system.risk.model import RiskConfig


@dataclass(frozen=True)
class ProjectPaths:
    root: Path
    config_dir: Path
    output_dir: Path


@dataclass(frozen=True)
class RuntimeConfig:
    base: dict[str, Any]
    risk: dict[str, Any]
    fees: dict[str, Any]
    sha256: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "base": self.base,
            "risk": self.risk,
            "fees": self.fees,
            "sha256": self.sha256,
        }

    def risk_config(self, *, initial_equity: float | None = None) -> RiskConfig:
        risk = self.risk.get("risk", {}) if isinstance(self.risk.get("risk"), dict) else {}
        equity = initial_equity
        if equity is None:
            equity = _float_value(risk, "per_symbol_initial_equity", RiskConfig.initial_equity)
        return RiskConfig(
            initial_equity=float(equity),
            halt_equity_ratio=_float_value(risk, "halt_equity_ratio", RiskConfig.halt_equity_ratio),
            max_leverage=_float_value(risk, "max_leverage", RiskConfig.max_leverage),
            single_position_loss_pct=_float_value(
                risk,
                "single_position_loss_pct",
                RiskConfig.single_position_loss_pct,
                aliases=("max_single_position_loss_pct", "max_position_loss_pct"),
            ),
            risk_per_trade_pct=_float_value(risk, "risk_per_trade_pct", RiskConfig.risk_per_trade_pct),
            margin_mode=str(risk.get("margin_mode", RiskConfig.margin_mode)),
            position_mode=str(risk.get("position_mode", RiskConfig.position_mode)),
            maintenance_margin_rate=_float_value(
                risk,
                "maintenance_margin_rate",
                RiskConfig.maintenance_margin_rate,
            ),
            liquidation_cost_buffer_pct=_float_value(
                risk,
                "liquidation_cost_buffer_pct",
                RiskConfig.liquidation_cost_buffer_pct,
                aliases=("cost_buffer_pct",),
            ),
            min_stop_distance_pct=_float_value(
                risk,
                "min_stop_distance_pct",
                RiskConfig.min_stop_distance_pct,
                aliases=("min_stop_distance", "stop_distance_pct", "stop_distance"),
            ),
            min_take_profit_distance_pct=_float_value(
                risk,
                "min_take_profit_distance_pct",
                RiskConfig.min_take_profit_distance_pct,
                aliases=("min_take_profit_distance", "take_profit_distance_pct", "take_profit_distance"),
            ),
            min_reward_to_risk=_float_value(
                risk,
                "min_reward_to_risk",
                RiskConfig.min_reward_to_risk,
                aliases=("min_rr", "reward_to_risk_min"),
            ),
            min_signal_score=_float_value(
                risk,
                "min_signal_score",
                RiskConfig.min_signal_score,
                aliases=("min_score", "signal_score_min"),
            ),
        )

    def cost_config(self) -> CostConfig:
        fees = self.fees.get("fees", {}) if isinstance(self.fees.get("fees"), dict) else {}
        slippage = self.fees.get("slippage", {}) if isinstance(self.fees.get("slippage"), dict) else {}
        funding = self.fees.get("funding", {}) if isinstance(self.fees.get("funding"), dict) else {}
        return CostConfig(
            taker_fee_rate=_float_value(fees, "taker_fee_rate", CostConfig.taker_fee_rate),
            maker_fee_rate=_float_value(fees, "maker_fee_rate", CostConfig.maker_fee_rate),
            default_use_taker=_bool_value(fees, "default_use_taker", CostConfig.default_use_taker),
            normal_slippage_bps=_float_value(slippage, "normal_bps", CostConfig.normal_slippage_bps),
            stress_slippage_bps=_float_value(slippage, "stress_bps", CostConfig.stress_slippage_bps),
            participation_tiers=_tuple_of_dicts(slippage.get("participation_tiers"), CostConfig().participation_tiers),
            funding_rate=_float_value(funding, "baseline_rate", CostConfig.funding_rate),
            funding_interval_hours=int(_float_value(funding, "baseline_hours", CostConfig.funding_interval_hours)),
            stress_funding_rates=_tuple_of_dicts(funding.get("stress_rates"), CostConfig().stress_funding_rates),
        )


def _value(mapping: dict[str, Any], key: str, default: Any, aliases: tuple[str, ...] = ()) -> Any:
    for name in (key, *aliases):
        if name in mapping:
            return mapping[name]
    return default


def _float_value(mapping: dict[str, Any], key: str, default: float, *, aliases: tuple[str, ...] = ()) -> float:
    value = _value(mapping, key, default, aliases)
    if isinstance(value, list):
        value = value[0] if value else default
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _bool_value(mapping: dict[str, Any], key: str, default: bool) -> bool:
    value = mapping.get(key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "y", "on"}:
            return True
        if lowered in {"false", "0", "no", "n", "off"}:
            return False
    return bool(default)


def _tuple_of_dicts(value: Any, default: tuple[dict[str, float], ...]) -> tuple[dict[str, float], ...]:
    if not isinstance(value, list):
        return default
    items: list[dict[str, float]] = []
    for item in value:
        if not isinstance(item, dict):
            return default
        items.append(dict(item))
    return tuple(items)


def project_paths(start: Path | None = None) -> ProjectPaths:
    root = package_project_root(start)

    # 打包后：配置文件在 _internal/config
    if getattr(sys, 'frozen', False):
        exe_dir = Path(sys.executable).parent
        config_dir = exe_dir / "_internal" / "config"
        if config_dir.exists():
            return ProjectPaths(root, config_dir, root / "outputs")

    return ProjectPaths(root, root / "config", root / "outputs")


def load_yaml(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError(f"yaml root must be a mapping: {path}")
    return data


def load_config(name: str) -> dict[str, Any]:
    paths = project_paths()
    return load_yaml(paths.config_dir / name)


def load_runtime_config() -> RuntimeConfig:
    paths = project_paths()
    base = load_yaml(paths.config_dir / "base.yaml")
    risk = load_yaml(paths.config_dir / "risk.yaml")
    fees = load_yaml(paths.config_dir / "fees.yaml")
    payload = {"base": base, "risk": risk, "fees": fees}
    digest = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return RuntimeConfig(base=base, risk=risk, fees=fees, sha256=digest)


def write_effective_config(output_dir: str | Path | None = None) -> Path:
    runtime_config = load_runtime_config()
    out = Path(output_dir) if output_dir else project_paths().output_dir
    out.mkdir(parents=True, exist_ok=True)
    path = out / "effective_config.json"
    path.write_text(
        json.dumps(runtime_config.as_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path
