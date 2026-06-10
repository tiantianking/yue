from __future__ import annotations

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


def project_paths(start: Path | None = None) -> ProjectPaths:
    root = package_project_root(start)
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
