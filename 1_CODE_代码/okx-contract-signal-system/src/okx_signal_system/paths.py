from __future__ import annotations

import sys
from pathlib import Path


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


def find_lightweight_history(dataset: str) -> Path:
    root = workspace_root()

    # 打包后：数据在 _internal/lightweight_history
    if getattr(sys, 'frozen', False):
        exe_dir = Path(sys.executable).parent
        packaged_data = exe_dir / "_internal" / "lightweight_history" / dataset
        if packaged_data.exists():
            return packaged_data

    matches = sorted(root.glob(f"*/lightweight_history/{dataset}"))
    existing = [path for path in matches if path.is_dir()]
    if not existing:
        raise FileNotFoundError(f"dataset not found under workspace: {dataset}")
    return existing[0]
