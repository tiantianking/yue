from __future__ import annotations

from pathlib import Path


def package_project_root(start: Path | None = None) -> Path:
    base = (start or Path(__file__)).resolve()
    for parent in [base, *base.parents]:
        if (parent / "pyproject.toml").exists():
            return parent
    raise RuntimeError("package project root not found")


def workspace_root(start: Path | None = None) -> Path:
    base = package_project_root(start)
    for parent in [base, *base.parents]:
        if (parent / "PROJECT_CONTROL").exists():
            return parent
    return base


def find_lightweight_history(dataset: str) -> Path:
    root = workspace_root()
    matches = sorted(root.glob(f"*/lightweight_history/{dataset}"))
    existing = [path for path in matches if path.is_dir()]
    if not existing:
        raise FileNotFoundError(f"dataset not found under workspace: {dataset}")
    return existing[0]
