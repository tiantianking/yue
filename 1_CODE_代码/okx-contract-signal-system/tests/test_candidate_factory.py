from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run_candidate_factory.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("run_candidate_factory", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_candidate_factory_cli_starts_from_source_checkout() -> None:
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "frozen gates" in completed.stdout


def test_discover_candidates_only_accepts_schema_v2(tmp_path: Path) -> None:
    module = _load_module()
    (tmp_path / "valid.json").write_text(
        json.dumps({"schema": "okx_pre_pnl_candidate_v2", "candidate_id": "valid"}),
        encoding="utf-8",
    )
    (tmp_path / "shadow.json").write_text(
        json.dumps({"schema": "okx_signal_shadow_candidate_v1", "candidate_id": "shadow"}),
        encoding="utf-8",
    )
    (tmp_path / "PRE_PNL_CANDIDATE_TEMPLATE.json").write_text(
        json.dumps({"schema": "okx_pre_pnl_candidate_v2", "candidate_id": "template"}),
        encoding="utf-8",
    )
    (tmp_path / "broken.json").write_text("not-json", encoding="utf-8")

    found = module.discover_candidates(tmp_path)
    assert [path.name for path, _payload in found] == ["valid.json"]


def test_factory_status_never_allows_auto_promotion(tmp_path: Path, monkeypatch) -> None:
    module = _load_module()
    candidate_dir = tmp_path / "candidates"
    candidate_dir.mkdir()
    candidate = candidate_dir / "candidate.json"
    candidate.write_text(
        json.dumps({"schema": "okx_pre_pnl_candidate_v2", "candidate_id": "candidate-a"}),
        encoding="utf-8",
    )

    def fake_run_candidate(*_args, **_kwargs):
        return {
            "candidate_id": "candidate-a",
            "stage": "PRE_PNL_GATE_PASSED",
            "gate_passed": True,
            "formal_a": False,
            "automatic_promotion": False,
            "automatic_ordering": False,
            "parameter_rescue_allowed": False,
        }

    monkeypatch.setattr(module, "run_candidate", fake_run_candidate)
    status_path = tmp_path / "factory_status.json"
    payload = module.run_factory(
        candidate_dir=candidate_dir,
        report_root=tmp_path / "reports",
        status_path=status_path,
        auto_archive=True,
    )

    assert payload["discovered_candidate_count"] == 1
    assert payload["policy"]["formal_a_allowed"] is False
    assert payload["policy"]["automatic_ordering"] is False
    assert status_path.is_file()
