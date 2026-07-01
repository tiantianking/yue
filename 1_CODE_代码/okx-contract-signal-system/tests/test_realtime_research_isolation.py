from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_SOURCES = [
    ROOT / "main.py",
    ROOT / "gui.py",
    ROOT / "src" / "okx_signal_system" / "scheduler.py",
    ROOT / "src" / "okx_signal_system" / "exchange" / "realtime.py",
    *sorted((ROOT / "src" / "okx_signal_system" / "signal_service").glob("*.py")),
]
BLOCKED_ROOTS = {
    "okx_signal_system.backtest",
    "okx_signal_system.research",
    "okx_signal_system.training",
    "okx_signal_system.ml",
}


def _blocked_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    blocked: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""]
        else:
            continue
        for name in names:
            if any(name == root or name.startswith(f"{root}.") for root in BLOCKED_ROOTS):
                blocked.append(f"{path.relative_to(ROOT)}:{name}")
    return blocked


def test_realtime_sources_do_not_import_research_or_ml_roots() -> None:
    blocked = [item for source in RUNTIME_SOURCES for item in _blocked_imports(source)]

    assert blocked == []


def test_realtime_imports_do_not_load_research_or_ml_roots() -> None:
    code = """
import json
import sys

import okx_signal_system.signal_service.job  # noqa: F401
import okx_signal_system.scheduler  # noqa: F401
import okx_signal_system.exchange.realtime  # noqa: F401

blocked_roots = ("okx_signal_system.backtest", "okx_signal_system.research", "okx_signal_system.training", "okx_signal_system.ml")
loaded = sorted(name for name in sys.modules if name.startswith(blocked_roots))
print(json.dumps(loaded))
raise SystemExit(1 if loaded else 0)
"""
    env = os.environ.copy()
    src = str(ROOT / "src")
    env["PYTHONPATH"] = src if not env.get("PYTHONPATH") else f"{src}{os.pathsep}{env['PYTHONPATH']}"

    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_runtime_sources_enqueue_notifications_instead_of_direct_send() -> None:
    direct_send_calls = {
        "send_b_tier_summary",
        "send_candidate_health_report",
        "send_status",
        "send_startup",
    }
    offenders: list[str] = []
    for source in RUNTIME_SOURCES:
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else None
            if name in direct_send_calls:
                offenders.append(f"{source.relative_to(ROOT)}:{node.lineno}:{name}")

    assert offenders == []
