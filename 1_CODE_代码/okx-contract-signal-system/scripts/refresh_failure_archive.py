from __future__ import annotations

import argparse
import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = PROJECT_ROOT.parents[1]
DEFAULT_RESEARCH_ROOTS = (
    WORKSPACE_ROOT / "HISTORY_PACKAGES_20260621" / "RESEARCH",
    PROJECT_ROOT / "outputs" / "failed_research",
)


def failure_archive_root() -> Path:
    configured = os.environ.get("FAILED_RESEARCH_ARCHIVE_DIR", "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path.home() / "Desktop" / "失败策略"


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    return payload if isinstance(payload, dict) else {}


def _is_failure(payload: dict[str, Any]) -> bool:
    decision = str(payload.get("decision") or payload.get("status") or "").upper()
    return any(token in decision for token in ("REJECT", "FAIL", "ARCHIVE", "NO_RESCUE"))


def _candidate_id(payload: dict[str, Any], path: Path) -> str:
    return str(payload.get("candidate_id") or payload.get("protocol_id") or path.stem)


def _short_label(candidate_id: str) -> str:
    match = re.match(r"([A-Z]{1,4}\d{1,4})", candidate_id.upper())
    if match:
        return match.group(1)
    safe = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in candidate_id)
    return safe[:48] or "UNKNOWN"


def _archive_date(payload: dict[str, Any], source: Path) -> str:
    for key in ("archived_at", "generated_at", "updated_at", "executed_at"):
        raw = str(payload.get(key) or "").strip()
        if raw:
            try:
                return datetime.fromisoformat(raw.replace("Z", "+00:00")).date().isoformat()
            except ValueError:
                pass
    return datetime.fromtimestamp(source.stat().st_mtime, tz=timezone.utc).date().isoformat()


def _failed_items(payload: dict[str, Any]) -> list[str]:
    values = payload.get("failed_gates") or payload.get("failed_checks") or []
    output: list[str] = []
    if isinstance(values, list):
        for item in values:
            if isinstance(item, dict):
                name = str(item.get("name") or item.get("check") or item.get("detail") or "").strip()
            else:
                name = str(item).strip()
            if name:
                output.append(name)
    return output


def _summary_markdown(payload: dict[str, Any], source: Path, destination: Path) -> str:
    candidate_id = _candidate_id(payload, source)
    decision = str(payload.get("decision") or payload.get("status") or "FAILED")
    pnl_opened = payload.get("pnl_opened")
    future_opened = payload.get("future_returns_opened")
    failed = _failed_items(payload)
    no_rescue = payload.get("no_rescue") if isinstance(payload.get("no_rescue"), list) else []
    lines = [
        f"# {candidate_id} 失败说明",
        "",
        f"- 决策：`{decision}`",
        f"- 来源：`{source}`",
        f"- 归档目录：`{destination}`",
        f"- PnL 是否打开：`{pnl_opened}`",
        f"- 未来收益是否打开：`{future_opened}`",
        "- 生产系统影响：`NONE`",
        "",
        "## 失败门禁",
        "",
    ]
    lines.extend(f"- {item}" for item in failed) if failed else lines.append("- 详见原始状态文件。")
    lines.extend(["", "## 禁止营救", ""])
    lines.extend(f"- {item}" for item in no_rescue) if no_rescue else lines.append("- 不得通过事后调参、删币、改方向或降低门槛重新测试。")
    lines.extend(["", "该策略已进入桌面失败策略永久归档，不得换名重新进入候选池。", ""])
    return "\n".join(lines)


def _related_evidence(source: Path) -> list[Path]:
    parent = source.parent
    patterns = (
        "*FINAL_STATUS.json",
        "*RESULT*.json",
        "*RESULT*.md",
        "*PROTOCOL*.json",
        "robustness_screen.json",
        "research_gate_report.json",
        "failure_summary.json",
    )
    found: dict[str, Path] = {}
    for pattern in patterns:
        for path in parent.glob(pattern):
            if path.is_file():
                found[path.name] = path
    return sorted(found.values(), key=lambda item: item.name)


def discover_failures(research_roots: tuple[Path, ...]) -> list[tuple[Path, dict[str, Any]]]:
    failures: dict[str, tuple[Path, dict[str, Any]]] = {}
    for root in research_roots:
        if not root.is_dir():
            continue
        candidates = list(root.rglob("*_FINAL_STATUS.json")) + list(root.rglob("failure_summary.json"))
        for path in candidates:
            try:
                payload = _load_json(path)
            except Exception:
                continue
            if not _is_failure(payload):
                continue
            candidate_id = _candidate_id(payload, path)
            failures[candidate_id] = (path, payload)
    return sorted(failures.values(), key=lambda item: _candidate_id(item[1], item[0]))


def refresh(*, research_roots: tuple[Path, ...] = DEFAULT_RESEARCH_ROOTS) -> dict[str, Any]:
    archive_root = failure_archive_root()
    archive_root.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, str]] = []

    for source, payload in discover_failures(research_roots):
        candidate_id = _candidate_id(payload, source)
        date = _archive_date(payload, source)
        destination = archive_root / f"{_short_label(candidate_id)}_失败归档_{date}"
        destination.mkdir(parents=True, exist_ok=True)
        for evidence in _related_evidence(source):
            shutil.copy2(evidence, destination / evidence.name)
        (destination / "失败说明.md").write_text(
            _summary_markdown(payload, source, destination),
            encoding="utf-8",
        )
        rows.append(
            {
                "candidate_id": candidate_id,
                "decision": str(payload.get("decision") or payload.get("status") or "FAILED"),
                "folder": destination.name,
            }
        )

    overview_dir = archive_root / "01_先看总览"
    overview_dir.mkdir(parents=True, exist_ok=True)
    index_lines = [
        "# 失败策略自动归档索引",
        "",
        f"最近刷新：{datetime.now().astimezone().isoformat(timespec='seconds')}",
        "",
        f"自动识别失败策略：{len(rows)} 个",
        "",
        "## 最近自动归档",
        "",
    ]
    for row in rows:
        index_lines.append(f"- `{row['candidate_id']}` → `{row['decision']}` → `{row['folder']}`")
    if not rows:
        index_lines.append("- 当前未发现新的可自动识别失败状态文件。")
    index_lines.extend(
        [
            "",
            "## 规则",
            "",
            "- 失败策略不得换名字重新进入候选池。",
            "- 不得通过事后调参、删币、改方向或降低成本营救。",
            "- 自动刷新只复制和补充文件，不删除历史归档。",
            "",
        ]
    )
    index_path = overview_dir / "自动归档索引.md"
    index_path.write_text("\n".join(index_lines), encoding="utf-8")
    return {
        "archive_root": str(archive_root),
        "failure_count": len(rows),
        "index": str(index_path),
        "rows": rows,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Copy failed research evidence into the user's Desktop failure-strategy archive.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    payload = refresh()
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"Archived {payload['failure_count']} failed strategies to {payload['archive_root']}")
        print(f"Index: {payload['index']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
