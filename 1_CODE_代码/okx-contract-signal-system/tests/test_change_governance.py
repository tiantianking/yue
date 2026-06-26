from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load_script(name: str):
    path = ROOT / "scripts" / name
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_governance_requires_documentation_and_version_files(tmp_path: Path, monkeypatch) -> None:
    module = _load_script("check_change_governance.py")
    archive = tmp_path / "Desktop" / "失败策略"
    archive.mkdir(parents=True)
    monkeypatch.setattr(module, "_failure_archive_root", lambda: archive)
    changed = {
        "src/okx_signal_system/signal_runtime.py",
        "pyproject.toml",
        "src/okx_signal_system/__init__.py",
        "src/okx_contract_signal_system.egg-info/PKG-INFO",
        "docs/PROJECT_OVERVIEW_CN.md",
        f"docs/V{module._read_version()}_RELEASE_CN.md",
    }
    monkeypatch.setattr(module, "_changed_project_files", lambda _upstream: changed)
    monkeypatch.setattr(module, "_project_prefix", lambda: "1_CODE_代码/okx-contract-signal-system")

    def fake_git(*args: str, check: bool = True) -> str:
        if args[:4] == ("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"):
            return "origin/master"
        if args == ("rev-parse", "HEAD"):
            return "abc123"
        if args == ("rev-parse", "origin/master"):
            return "abc123"
        if args[:2] == ("rev-list", "--left-right"):
            return "0\t0"
        if args[:2] == ("status", "--porcelain"):
            return ""
        return ""

    monkeypatch.setattr(module, "_git", fake_git)
    checks = module.evaluate(require_github_sync=True)

    assert all(item.ok for item in checks), [item for item in checks if not item.ok]


def test_failure_archive_refresh_copies_evidence_and_writes_index(tmp_path: Path, monkeypatch) -> None:
    module = _load_script("refresh_failure_archive.py")
    research = tmp_path / "research" / "h99"
    research.mkdir(parents=True)
    status = research / "H99_FINAL_STATUS.json"
    status.write_text(
        json.dumps(
            {
                "candidate_id": "H99_TEST_FAILURE_V1",
                "decision": "REJECT_H99_BEFORE_PNL_NO_RESCUE",
                "pnl_opened": False,
                "future_returns_opened": False,
                "failed_gates": ["duplicate_family", "cost_stress"],
                "no_rescue": ["do not tune parameters"],
            }
        ),
        encoding="utf-8",
    )
    (research / "H99_RESULTS_CN.md").write_text("# H99 result", encoding="utf-8")
    archive = tmp_path / "Desktop" / "失败策略"
    monkeypatch.setattr(module, "failure_archive_root", lambda: archive)

    payload = module.refresh(research_roots=(tmp_path / "research",))

    destination = archive / next(item["folder"] for item in payload["rows"])
    assert payload["failure_count"] == 1
    assert (destination / "H99_FINAL_STATUS.json").is_file()
    assert (destination / "H99_RESULTS_CN.md").is_file()
    assert (destination / "失败说明.md").is_file()
    assert (archive / "01_先看总览" / "自动归档索引.md").is_file()
