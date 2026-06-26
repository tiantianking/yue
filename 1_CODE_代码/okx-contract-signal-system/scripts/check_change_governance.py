from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
VERSION_FILES = {
    "pyproject.toml",
    "src/okx_signal_system/__init__.py",
    "src/okx_contract_signal_system.egg-info/PKG-INFO",
}
BEHAVIORAL_PREFIXES = ("src/", "scripts/", "config/", "dashboard/", "deployment/")
BEHAVIORAL_FILES = {
    "main.py",
    "gui.py",
    "start.bat",
    "pyproject.toml",
    "requirements.txt",
    "requirements.lock",
    "okx_signal.spec",
}


@dataclass(frozen=True)
class GovernanceCheck:
    name: str
    ok: bool
    detail: str


def _git(*args: str, check: bool = True) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if check and completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or f"git exited {completed.returncode}"
        raise RuntimeError(detail)
    return completed.stdout.strip()


def _project_prefix() -> str:
    repository_root = Path(_git("rev-parse", "--show-toplevel")).resolve()
    return PROJECT_ROOT.resolve().relative_to(repository_root).as_posix()


def _relative_project_path(path: str, project_prefix: str) -> str | None:
    normalized = path.replace("\\", "/").strip()
    prefix = project_prefix.rstrip("/") + "/"
    if normalized == project_prefix.rstrip("/"):
        return ""
    if normalized.startswith(prefix):
        return normalized[len(prefix) :]
    if normalized and not normalized.startswith("../"):
        return normalized
    return None


def _changed_project_files(upstream: str) -> set[str]:
    project_prefix = _project_prefix()
    commands = [
        ("-c", "core.quotepath=false", "diff", "--name-only", f"{upstream}...HEAD", "--", "."),
        ("-c", "core.quotepath=false", "diff", "--name-only", "--", "."),
        ("-c", "core.quotepath=false", "diff", "--cached", "--name-only", "--", "."),
        ("-c", "core.quotepath=false", "ls-files", "--others", "--exclude-standard", "--", "."),
    ]
    changed: set[str] = set()
    for command in commands:
        output = _git(*command, check=False)
        for line in output.splitlines():
            relative = _relative_project_path(line, project_prefix)
            if relative:
                changed.add(relative)
    return changed


def _read_version() -> str:
    payload = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return str(payload["project"]["version"])


def _package_version() -> str:
    namespace: dict[str, object] = {}
    exec((PROJECT_ROOT / "src" / "okx_signal_system" / "__init__.py").read_text(encoding="utf-8"), namespace)
    return str(namespace.get("__version__", ""))


def _pkg_info_version() -> str:
    for line in (PROJECT_ROOT / "src" / "okx_contract_signal_system.egg-info" / "PKG-INFO").read_text(encoding="utf-8").splitlines():
        if line.startswith("Version:"):
            return line.split(":", 1)[1].strip()
    return ""


def _failure_archive_root() -> Path:
    configured = os.environ.get("FAILED_RESEARCH_ARCHIVE_DIR", "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path.home() / "Desktop" / "失败策略"


def _is_behavioral(path: str) -> bool:
    return path in BEHAVIORAL_FILES or path.startswith(BEHAVIORAL_PREFIXES)


def evaluate(*, require_github_sync: bool = False) -> list[GovernanceCheck]:
    checks: list[GovernanceCheck] = []
    version = _read_version()
    package_version = _package_version()
    pkg_info_version = _pkg_info_version()
    checks.append(
        GovernanceCheck(
            "version_consistent",
            version == package_version == pkg_info_version,
            f"pyproject={version} package={package_version} pkg_info={pkg_info_version}",
        )
    )

    overview_path = PROJECT_ROOT / "docs" / "PROJECT_OVERVIEW_CN.md"
    policy_path = PROJECT_ROOT / "docs" / "CHANGE_CONTROL_POLICY_CN.md"
    release_note_path = PROJECT_ROOT / "docs" / f"V{version}_RELEASE_CN.md"
    overview_text = overview_path.read_text(encoding="utf-8") if overview_path.is_file() else ""
    checks.append(
        GovernanceCheck(
            "project_overview_current",
            overview_path.is_file() and f"当前版本：v{version}" in overview_text,
            str(overview_path),
        )
    )
    checks.append(GovernanceCheck("change_policy_present", policy_path.is_file(), str(policy_path)))
    checks.append(GovernanceCheck("current_release_note_present", release_note_path.is_file(), str(release_note_path)))

    release_files = {
        line.strip().replace("\\", "/")
        for line in (PROJECT_ROOT / "RELEASE_FILES.txt").read_text(encoding="utf-8-sig").splitlines()
        if line.strip()
    }
    required_release_entries = {
        "docs/PROJECT_OVERVIEW_CN.md",
        "docs/CHANGE_CONTROL_POLICY_CN.md",
        f"docs/V{version}_RELEASE_CN.md",
        "scripts/check_change_governance.py",
        "scripts/refresh_failure_archive.py",
        "CHECK_REMOTE_SYNC.cmd",
    }
    missing_release_entries = sorted(required_release_entries - release_files)
    checks.append(
        GovernanceCheck(
            "governance_files_released",
            not missing_release_entries,
            ", ".join(missing_release_entries) or "complete",
        )
    )

    archive_root = _failure_archive_root()
    checks.append(
        GovernanceCheck(
            "desktop_failure_archive_ready",
            archive_root.is_dir() and archive_root.name == "失败策略",
            str(archive_root),
        )
    )

    upstream = _git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}", check=False)
    checks.append(GovernanceCheck("git_upstream_configured", bool(upstream), upstream or "missing"))
    changed: set[str] = set()
    if upstream:
        changed = _changed_project_files(upstream)
        behavioral = sorted(path for path in changed if _is_behavioral(path))
        if behavioral:
            required_changed = {
                "docs/PROJECT_OVERVIEW_CN.md",
                f"docs/V{version}_RELEASE_CN.md",
                *VERSION_FILES,
            }
            missing_changed = sorted(required_changed - changed)
            checks.append(
                GovernanceCheck(
                    "behavioral_change_documented_and_versioned",
                    not missing_changed,
                    f"behavioral={behavioral}; missing={missing_changed}",
                )
            )
        else:
            checks.append(GovernanceCheck("behavioral_change_documented_and_versioned", True, "no behavioral changes"))

        head = _git("rev-parse", "HEAD")
        upstream_head = _git("rev-parse", upstream, check=False)
        ahead_behind = _git("rev-list", "--left-right", "--count", f"{upstream}...HEAD", check=False)
        synchronized = bool(upstream_head) and head == upstream_head
        checks.append(
            GovernanceCheck(
                "github_tracking_branch_synchronized",
                synchronized if require_github_sync else True,
                f"upstream={upstream} ahead_behind={ahead_behind or 'unknown'} head={head[:12]} upstream_head={upstream_head[:12] if upstream_head else 'missing'} required={require_github_sync}",
            )
        )

        if require_github_sync:
            dirty = _git("-c", "core.quotepath=false", "status", "--porcelain", "--", ".", check=False)
            checks.append(GovernanceCheck("project_worktree_clean", not dirty, dirty or "clean"))

    return checks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify project documentation, failure archive, versioning, and GitHub synchronization governance.")
    parser.add_argument("--require-github-sync", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        checks = evaluate(require_github_sync=args.require_github_sync)
    except Exception as exc:
        checks = [GovernanceCheck("governance_execution", False, str(exc))]

    failed = [item for item in checks if not item.ok]
    if args.json:
        print(json.dumps({"ok": not failed, "checks": [asdict(item) for item in checks]}, ensure_ascii=False, indent=2))
    else:
        for item in checks:
            print(f"[{'PASS' if item.ok else 'FAIL'}] {item.name}: {item.detail}")
        print("CHANGE GOVERNANCE PASSED" if not failed else "CHANGE GOVERNANCE FAILED")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
