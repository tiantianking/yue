from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from okx_signal_system import __version__
from okx_signal_system.config import env_bool, load_config
from okx_signal_system.research.approved_strategy_manifest import load_approved_manifest_status


@dataclass(frozen=True)
class CheckResult:
    name: str
    ok: bool
    detail: str
    blocking: bool = True


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key and key not in os.environ:
            os.environ[key] = value


def _writable_directory(path: Path) -> tuple[bool, str]:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".preflight-write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True, str(path)
    except Exception as exc:
        return False, f"{path}: {exc}"


def _explicit_safety_environment() -> tuple[bool, str]:
    expected = {
        "SIGNAL_ONLY": True,
        "DATA_READ_ONLY": True,
        "OKX_AUTO_CLOSE_ENABLED": False,
    }
    problems: list[str] = []
    for name, expected_value in expected.items():
        raw = os.environ.get(name)
        if raw is None:
            problems.append(f"{name}=missing")
            continue
        lowered = raw.strip().lower()
        if lowered not in {"true", "1", "yes", "y", "on", "false", "0", "no", "n", "off"}:
            problems.append(f"{name}=invalid")
            continue
        actual = lowered in {"true", "1", "yes", "y", "on"}
        if actual is not expected_value:
            problems.append(f"{name}={raw}")
    if problems:
        return False, ", ".join(problems)
    return True, "critical safety variables are explicit"


def _private_credentials_empty() -> tuple[bool, str]:
    present = [
        key
        for key in ("OKX_API_KEY", "OKX_SECRET_KEY", "OKX_PASSPHRASE")
        if os.environ.get(key, "").strip()
    ]
    if present:
        return False, "private OKX credentials must be empty in SIGNAL_ONLY deployment: " + ", ".join(present)
    return True, "no private OKX credentials configured"


def run_preflight(mode: str, env_file: Path) -> list[CheckResult]:
    _load_env_file(env_file)
    base = load_config("base.yaml")
    execution = base.get("execution", {}) if isinstance(base.get("execution"), dict) else {}
    feishu_cfg = base.get("feishu", {}) if isinstance(base.get("feishu"), dict) else {}

    results: list[CheckResult] = []
    results.append(CheckResult("python_version", sys.version_info >= (3, 11), sys.version.split()[0]))
    results.append(CheckResult("package_version", bool(__version__), __version__))
    results.append(CheckResult("signal_only", env_bool("SIGNAL_ONLY", True), "SIGNAL_ONLY must be true"))
    results.append(CheckResult("data_read_only", env_bool("DATA_READ_ONLY", True), "DATA_READ_ONLY must be true"))
    results.append(CheckResult("auto_close_disabled", not env_bool("OKX_AUTO_CLOSE_ENABLED", False), "OKX_AUTO_CLOSE_ENABLED must be false"))
    results.append(CheckResult("live_order_disabled", execution.get("live_order_enabled") is False, str(execution.get("live_order_enabled"))))
    results.append(CheckResult("dry_run_enabled", execution.get("dry_run_enabled") is True, str(execution.get("dry_run_enabled"))))

    explicit_ok, explicit_detail = _explicit_safety_environment()
    results.append(CheckResult("explicit_safety_environment", explicit_ok, explicit_detail, blocking=mode == "production"))

    credentials_ok, credentials_detail = _private_credentials_empty()
    results.append(CheckResult("private_credentials_empty", credentials_ok, credentials_detail))

    runtime_cache = Path(os.environ.get("JIAOYI_RUNTIME_CACHE_DIR", PROJECT_ROOT / "outputs" / "runtime_cache")).expanduser()
    for name, path in (
        ("outputs_writable", PROJECT_ROOT / "outputs"),
        ("logs_writable", PROJECT_ROOT / "logs"),
        ("runtime_cache_writable", runtime_cache),
    ):
        ok, detail = _writable_directory(path)
        results.append(CheckResult(name, ok, detail))

    webhook_enabled = env_bool("FEISHU_ENABLED", bool(feishu_cfg.get("enabled", True)))
    webhook_set = bool(os.environ.get("FEISHU_WEBHOOK_URL", "").strip())
    feishu_ok = webhook_enabled and webhook_set if mode == "production" else ((not webhook_enabled) or webhook_set)
    results.append(
        CheckResult(
            "feishu_configuration",
            feishu_ok,
            "disabled" if not webhook_enabled else ("webhook configured" if webhook_set else "FEISHU_WEBHOOK_URL missing"),
            blocking=mode == "production",
        )
    )

    manifest_status = load_approved_manifest_status()
    manifest_required = mode == "production"
    results.append(
        CheckResult(
            "approved_manifest",
            manifest_status.ok,
            manifest_status.reason,
            blocking=manifest_required,
        )
    )

    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate an OKX signal-only deployment before service startup.")
    parser.add_argument("--mode", choices=("observation", "production"), default=os.environ.get("DEPLOYMENT_MODE", "observation"))
    parser.add_argument("--env-file", type=Path, default=PROJECT_ROOT / ".env")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    results = run_preflight(args.mode, args.env_file)
    failed = [item for item in results if item.blocking and not item.ok]

    if args.json:
        print(json.dumps({"mode": args.mode, "ok": not failed, "checks": [asdict(item) for item in results]}, ensure_ascii=False, indent=2))
    else:
        print(f"OKX signal deployment preflight: mode={args.mode} version={__version__}")
        for item in results:
            state = "PASS" if item.ok else ("FAIL" if item.blocking else "WARN")
            print(f"[{state}] {item.name}: {item.detail}")
        print("PRECHECK PASSED" if not failed else "PRECHECK FAILED")

    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
