from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import yaml


def package_project_root(start: Path | None = None) -> Path:
    base = (start or Path(__file__)).resolve()

    # 打包后的路径: exe所在目录/_internal/okx_signal_system/...
    # 正常开发: .../src/okx_signal_system/...
    exe_dir = Path(sys.executable).parent if getattr(sys, 'frozen', False) else None

    for parent in [base, *base.parents]:
        if (parent / "pyproject.toml").exists():
            return parent

    # 打包后：使用exe所在目录作为项目根
    if exe_dir:
        # exe在 OKXSignalSystem/OKXSignalSystem.exe, 项目根是 OKXSignalSystem/
        return exe_dir.parent

    raise RuntimeError("package project root not found")


def workspace_root(start: Path | None = None) -> Path:
    base = package_project_root(start)
    for parent in [base, *base.parents]:
        if (parent / "PROJECT_CONTROL").exists():
            return parent
    return base


def _packaged_roots() -> list[Path]:
    if not getattr(sys, 'frozen', False):
        return []

    roots: list[Path] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        roots.append(Path(meipass))
    roots.append(Path(sys.executable).parent / "_internal")
    return roots


def _data_cfg_from_config() -> dict[str, Any]:
    config_dirs: list[Path] = []
    for packaged_root in _packaged_roots():
        config_dirs.append(packaged_root / "config")

    try:
        config_dirs.append(package_project_root() / "config")
    except RuntimeError:
        pass

    for config_dir in config_dirs:
        config_path = config_dir / "base.yaml"
        if not config_path.exists():
            continue
        with config_path.open("r", encoding="utf-8") as handle:
            config = yaml.safe_load(handle) or {}
        data_cfg: Any = config.get("data", {}) if isinstance(config, dict) else {}
        if isinstance(data_cfg, dict):
            return data_cfg

    return {}


def _data_root_from_config() -> Path | None:
    data_cfg = _data_cfg_from_config()
    root_dir = data_cfg.get("historical_data_root") or data_cfg.get("root_dir")
    if root_dir:
        return Path(str(root_dir)).expanduser()
    return None


def _runtime_cache_root_from_config() -> Path | None:
    data_cfg = _data_cfg_from_config()
    root_dir = data_cfg.get("runtime_cache_root")
    if root_dir:
        return Path(str(root_dir)).expanduser()
    return None


def _dataset_under_data_root(data_root: Path, dataset: str) -> Path:
    if data_root.name == dataset:
        return data_root
    if data_root.name == "lightweight_history":
        return data_root / dataset
    return data_root / "lightweight_history" / dataset


def _configured_data_root(root_dir: Path | str | None = None) -> Path | None:
    if root_dir is not None:
        return Path(root_dir).expanduser()

    env_root = os.environ.get("JIAOYI_DATA_DIR")
    if env_root:
        return Path(env_root).expanduser()

    return _data_root_from_config()


def find_lightweight_history(dataset: str, root_dir: Path | str | None = None) -> Path:
    data_root = _configured_data_root(root_dir)
    if data_root is not None:
        dataset_path = _dataset_under_data_root(data_root, dataset)
        if dataset_path.is_dir():
            return dataset_path
        raise FileNotFoundError(f"dataset not found under data root: {dataset} ({data_root})")

    root = workspace_root()

    # 打包后：数据在 _internal/lightweight_history
    for packaged_root in _packaged_roots():
        packaged_data = packaged_root / "lightweight_history" / dataset
        if packaged_data.exists():
            return packaged_data

    matches = sorted(root.glob(f"*/lightweight_history/{dataset}"))
    existing = [path for path in matches if path.is_dir()]
    if not existing:
        raise FileNotFoundError(f"dataset not found under workspace: {dataset}")
    return existing[0]


def find_runtime_cache_root(dataset: str, root_dir: Path | str | None = None, *, create: bool = True) -> Path:
    if root_dir is not None:
        cache_root = Path(root_dir).expanduser()
    else:
        env_root = os.environ.get("JIAOYI_RUNTIME_CACHE_DIR")
        configured = _runtime_cache_root_from_config()
        if env_root:
            cache_root = Path(env_root).expanduser()
        elif configured is not None:
            cache_root = configured
        else:
            cache_root = package_project_root() / "outputs" / "runtime_cache"

    dataset_path = _dataset_under_data_root(cache_root, dataset)
    if create:
        dataset_path.mkdir(parents=True, exist_ok=True)
    return dataset_path
