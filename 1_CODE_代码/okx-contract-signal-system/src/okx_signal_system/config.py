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
            margin_mode=str(risk.get("margin_mode", RiskConfig.margin_mode)),
            position_mode=str(risk.get("position_mode", RiskConfig.position_mode)),
        )

    def cost_config(self) -> CostConfig:
        fees = self.fees.get("fees", {}) if isinstance(self.fees.get("fees"), dict) else {}
        slippage = self.fees.get("slippage", {}) if isinstance(self.fees.get("slippage"), dict) else {}
        funding = self.fees.get("funding", {}) if isinstance(self.fees.get("funding"), dict) else {}
        return CostConfig(
            taker_fee_rate=_float_value(fees, "taker_fee_rate", CostConfig.taker_fee_rate),
            normal_slippage_bps=_float_value(slippage, "normal_bps", CostConfig.normal_slippage_bps),
            stress_slippage_bps=_float_value(slippage, "stress_bps", CostConfig.stress_slippage_bps),
            funding_rate=_float_value(funding, "baseline_rate", CostConfig.funding_rate),
            funding_interval_hours=int(_float_value(funding, "baseline_hours", CostConfig.funding_interval_hours)),
        )


def _float_value(mapping: dict[str, Any], key: str, default: float) -> float:
    value = mapping.get(key, default)
    if isinstance(value, list):
        value = value[0] if value else default
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


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
