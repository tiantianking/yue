from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from okx_signal_system.paths import package_project_root


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
