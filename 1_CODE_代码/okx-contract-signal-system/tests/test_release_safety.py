from __future__ import annotations

import re
import tomllib
from pathlib import Path
from zipfile import ZipFile

import okx_signal_system
import pytest
from scripts.build_release_zip import build_release_zip, write_sha256_file
from okx_signal_system.config import load_config


ROOT = Path(__file__).resolve().parents[1]
RELEASE_TEXT_FILES = [
    "README.md",
    "docs/RELEASE_SAFETY.md",
    "docs/RUNTIME_VERIFICATION.md",
    "docs/SYSTEM_ARCHITECTURE.md",
    "docs/V3.56.6_RUNTIME_OBSERVATION_CN.md",
    "docs/V3.56.6_STALE_SYMBOL_AUDIT_CN.md",
    "docs/V3.56.7_DASHBOARD_HEALTH_GUARD_CN.md",
    "docs/V3.56.7_FULL_SOURCE_PACKAGE_OPERATIONS_CN.md",
    "docs/V3.56.7_SOURCE_PACKAGE_SAFETY_AUDIT_CN.md",
    "docs/V3.56.7_UPDATE_PACKAGE_README_CN.md",
    "src/okx_signal_system/reporting/report_builder.py",
    "src/okx_signal_system/signal_service/app.py",
    "src/okx_signal_system/notify/feishu.py",
    "okx_signal.spec",
]
PRIVATE_OKX_TOKENS = [
    "OKX_API_KEY",
    "OKX_SECRET_KEY",
    "OKX_PASSPHRASE",
]
RELEASE_ARTIFACT_EXCLUDES = [
    ".env",
    ".env.*",
    "build.log",
    "*.log",
    "logs/",
    "okx_signal.lock",
    ".pytest_cache/",
    "__pycache__/",
    "*.py[cod]",
    "output/",
    "outputs/*",
    "*.sqlite",
    "*.sqlite3",
    "*.db",
    "*.sqlite-wal",
    "*.sqlite-shm",
    "*.sqlite-journal",
    "*.sqlite3-wal",
    "*.sqlite3-shm",
    "*.sqlite3-journal",
    "*.db-wal",
    "*.db-shm",
    "*.db-journal",
]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _release_text() -> str:
    return "\n".join(_read(path) for path in RELEASE_TEXT_FILES)


def _manifest_lines() -> list[str]:
    return [line.strip() for line in _read("MANIFEST.in").splitlines() if line.strip()]


def _gitattributes_lines() -> list[str]:
    return [line.strip() for line in _read(".gitattributes").splitlines() if line.strip()]


def test_release_tests_package_can_import_integration_helpers() -> None:
    assert (ROOT / "tests" / "__init__.py").is_file()

    from tests._integration import require_lightweight_history

    assert callable(require_lightweight_history)


def test_release_version_sources_stay_consistent() -> None:
    from okx_signal_system.research.approved_strategy_manifest import APPROVED_RESEARCH_VERSION, APPROVED_STRATEGY_VERSION

    pyproject = tomllib.loads(_read("pyproject.toml"))
    package_version = okx_signal_system.__version__
    pkg_info = _read("src/okx_contract_signal_system.egg-info/PKG-INFO")
    main_text = _read("main.py")
    gui_text = _read("gui.py")
    start_text = _read("start.bat")

    assert package_version == "3.56.27"
    assert pyproject["project"]["version"] == package_version
    assert APPROVED_STRATEGY_VERSION == "3.56.15"
    assert f"Version: {package_version}" in pkg_info
    assert "from okx_signal_system import __version__ as _PACKAGE_VERSION" in main_text
    assert 'APP_VERSION = f"v{_PACKAGE_VERSION}"' in main_text
    assert "from okx_signal_system import __version__ as _PACKAGE_VERSION" in gui_text
    assert 'APP_VERSION = f"v{_PACKAGE_VERSION}"' in gui_text
    assert "from okx_signal_system import __version__; print('v' + __version__)" in start_text
    assert "title OKX Signal Platform %APP_VERSION%" in start_text
    assert "echo  OKX Signal Platform %APP_VERSION%" in start_text

    for text in [main_text, gui_text, start_text]:
        assert not re.search(r"v\d+\.\d+(?:\.\d+)?", text)


def test_strict_research_default_version_matches_cli_release() -> None:
    from okx_signal_system.backtest import research
    from okx_signal_system.research.approved_strategy_manifest import APPROVED_RESEARCH_VERSION

    cli_text = _read("src/okx_signal_system/backtest/research_cli.py")
    default_version = research.run_dataset_research_artifacts.__kwdefaults__["research_version"]

    assert default_version == "v3.56-strict"
    assert APPROVED_RESEARCH_VERSION == default_version
    assert f'parser.add_argument("--research-version", default="{default_version}")' in cli_text


def test_research_package_exports_runtime_manifest_paths() -> None:
    import okx_signal_system.research as research

    assert callable(research.approved_manifest_path)
    assert callable(research.candidate_params_path)
    assert callable(research.research_run_dir)


def test_research_package_is_in_distribution_sources() -> None:
    sources = _read("src/okx_contract_signal_system.egg-info/SOURCES.txt")

    for path in [
        "src/okx_signal_system/research/__init__.py",
        "src/okx_signal_system/research/approved_strategy_manifest.py",
        "src/okx_signal_system/research/downside_risk_weighting.py",
        "src/okx_signal_system/research/funding_carry_tilt.py",
        "src/okx_signal_system/research/fixed_cadence_momentum.py",
        "src/okx_signal_system/research/liquidity_admission_momentum.py",
        "src/okx_signal_system/research/membership_change_rebalance.py",
        "src/okx_signal_system/research/parallel_acceptance.py",
        "src/okx_signal_system/research/promote.py",
        "src/okx_signal_system/research/rank_conviction_weighting.py",
        "src/okx_signal_system/research/sector_balanced_momentum.py",
        "src/okx_signal_system/research/shadow_ensemble_acceptance.py",
    ]:
        assert path in sources


def test_distribution_sources_cover_all_python_modules_and_release_docs() -> None:
    sources = {
        line.strip()
        for line in _read("src/okx_contract_signal_system.egg-info/SOURCES.txt").splitlines()
        if line.strip()
    }
    expected = {
        path.relative_to(ROOT).as_posix()
        for base in [ROOT / "src" / "okx_signal_system", ROOT / "tests"]
        for path in base.rglob("*.py")
    }
    expected.update(path.relative_to(ROOT).as_posix() for path in (ROOT / "docs").glob("*.md"))

    assert expected - sources == set()


def test_release_file_manifest_is_present_and_self_including() -> None:
    manifest_path = ROOT / "RELEASE_FILES.txt"
    lines = [
        line.strip()
        for line in manifest_path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]

    assert "RELEASE_FILES.txt" in lines
    assert "CHECK_REMOTE_SYNC.cmd" in lines
    assert "scripts/build_release_zip.py" in lines
    assert "scripts/check_change_governance.py" in lines
    assert "scripts/refresh_failure_archive.py" in lines
    assert "scripts/system_check.py" in lines
    assert "scripts/run_candidate_factory.py" in lines
    assert "scripts/run_parallel_acceptance.py" in lines
    assert "scripts/update_shadow_ensemble_acceptance.py" in lines
    assert "scripts/update_momentum_fixed_3d_shadow.py" in lines
    assert "scripts/update_momentum_staggered_3x3_shadow.py" in lines
    assert "config/parallel_acceptance.yaml" in lines
    assert "config/parallel_acceptance_early_stop_protocol.json" in lines
    assert "config/shadow_ensemble_forward_acceptance_protocol.json" in lines
    assert "RUN_PARALLEL_ACCEPTANCE.cmd" in lines
    assert "scripts/preflight_check.py" not in lines
    assert "scripts/runtime_healthcheck.py" not in lines
    assert "scripts/check_shadow_ensemble_local.py" not in lines
    assert "deployment/install_linux.sh" in lines
    assert "deployment/systemd/okx-signal.service" in lines
    assert "deployment/systemd/okx-signal-health.service" in lines
    assert "deployment/systemd/okx-signal-health.timer" in lines
    assert "deployment/logrotate/okx-signal" in lines
    assert "deployment/okx-signal.env.example" in lines
    assert "docs/CHANGE_CONTROL_POLICY_CN.md" in lines
    assert "docs/DEPLOYMENT_CHECKLIST_CN.md" in lines
    assert "docs/PROJECT_OVERVIEW_CN.md" in lines
    assert "docs/V3.56.27_RELEASE_CN.md" in lines
    assert len(lines) == len(set(lines))
    assert all("\\" not in line and not line.startswith("/") and ".." not in Path(line).parts for line in lines)


def test_dashboard_uses_validated_runtime_snapshot_and_never_legacy_param_overrides() -> None:
    server_data = _read("dashboard/src/lib/server-data.ts")
    readme = _read("dashboard/README.md")

    assert 'path.join(outputsDir, "latest_scan_status.json")' in server_data
    assert "enrichedLatestScan?.selected_params" in server_data
    assert "enrichedLatestScan?.push_allowed === true" in server_data
    assert 'path.join(outputsDir, "selected_params.json")' not in server_data
    assert "quality.selected_params" not in server_data
    assert "Boolean(quality.push_allowed)" not in server_data
    assert "`outputs/runtime/approved_strategy_manifest.json`" in readme


def test_experimental_daily_learning_candidate_cannot_shadow_formal_research_candidate() -> None:
    daily_learning = _read("src/okx_signal_system/training/daily_learning.py")
    runtime_loader = _read("src/okx_signal_system/signal_service/runtime.py")

    assert 'DAILY_LEARNING_CANDIDATE_FILENAME = "daily_learning_candidate.json"' in daily_learning
    assert '"artifact_type": "experimental_daily_learning_candidate"' in daily_learning
    assert 'out / "candidate_params.json"' not in daily_learning
    assert "candidate_params.json" not in runtime_loader


def test_env_example_is_signal_only_and_has_no_private_okx_keys() -> None:
    text = _read(".env.example")

    assert "SIGNAL_ONLY=true" in text
    assert "DATA_READ_ONLY=true" in text
    assert "FEISHU_ENABLED=false" in text
    assert "FEISHU_WEBHOOK_URL=" in text
    assert "OKX_AUTO_CLOSE_ENABLED=true" not in text
    for token in PRIVATE_OKX_TOKENS:
        assert token not in text


def test_release_defaults_are_signal_only_read_only_notifications() -> None:
    cfg = load_config("base.yaml")

    assert cfg["project"]["mode"] == "SIGNAL_ONLY"
    assert cfg["data"]["read_only"] is True
    assert cfg["data"]["root_dir"] in {None, ""}
    assert cfg["execution"]["live_order_enabled"] is False
    assert cfg["execution"]["auto_close_enabled"] is False
    assert cfg["execution"]["dry_run_enabled"] is True
    assert cfg["feishu"]["enabled"] is True


def test_pyinstaller_release_keeps_example_and_excludes_real_env() -> None:
    spec = _read("okx_signal.spec")

    assert "('.env.example', '.')" in spec
    assert "('.env', '.')" not in spec
    assert '(".env", ".")' not in spec
    for token in PRIVATE_OKX_TOKENS:
        assert token not in spec


def test_gitignore_blocks_real_env_but_keeps_example() -> None:
    lines = [line.strip() for line in _read(".gitignore").splitlines()]

    assert ".env" in lines
    assert ".env.*" in lines
    assert "!.env.example" in lines
    for pattern in RELEASE_ARTIFACT_EXCLUDES:
        assert pattern in lines


def test_source_distribution_manifest_excludes_runtime_artifacts() -> None:
    manifest = "\n".join(_manifest_lines())

    for pattern in [
        ".env .env.*",
        "*.py[cod]",
        "*.sqlite *.sqlite3 *.db",
        "*.sqlite-wal *.sqlite-shm *.sqlite-journal",
        "*.sqlite3-wal *.sqlite3-shm *.sqlite3-journal",
        "*.db-wal *.db-shm *.db-journal",
        "build.log",
        "*.log",
        "okx_signal.lock",
    ]:
        assert pattern in manifest
    for directory in [".pytest_cache", "__pycache__", "logs", "output", "outputs"]:
        assert f"prune {directory}" in manifest


def test_source_archive_export_ignores_runtime_artifacts() -> None:
    lines = _gitattributes_lines()

    for pattern in [
        ".env export-ignore",
        ".env.* export-ignore",
        ".env.example -export-ignore",
        "build.log export-ignore",
        ".pytest_cache/** export-ignore",
        "__pycache__/** export-ignore",
        "**/__pycache__/** export-ignore",
        "logs/** export-ignore",
        "output/** export-ignore",
        "outputs/** export-ignore",
        "*.pyc export-ignore",
        "*.pyo export-ignore",
        "*.sqlite export-ignore",
        "*.sqlite3 export-ignore",
        "*.db export-ignore",
        "*.sqlite-wal export-ignore",
        "*.sqlite-shm export-ignore",
        "*.sqlite-journal export-ignore",
        "*.sqlite3-wal export-ignore",
        "*.sqlite3-shm export-ignore",
        "*.sqlite3-journal export-ignore",
        "*.db-wal export-ignore",
        "*.db-shm export-ignore",
        "*.db-journal export-ignore",
        "okx_signal.lock export-ignore",
    ]:
        assert pattern in lines


def test_release_zip_entries_use_posix_paths(tmp_path: Path) -> None:
    nested_file = tmp_path / "nested" / "release" / "file.txt"
    nested_file.parent.mkdir(parents=True)
    nested_file.write_text("release", encoding="utf-8")
    output_zip = tmp_path / "release.zip"

    build_release_zip(tmp_path, output_zip, paths=[nested_file.relative_to(tmp_path)])

    with ZipFile(output_zip) as archive:
        names = archive.namelist()

    assert names
    assert all("\\" not in name for name in names)
    assert any("/" in name for name in names)


def test_release_zip_sha256_sidecar_uses_release_naming(tmp_path: Path) -> None:
    output_zip = tmp_path / "release.zip"
    output_zip.write_bytes(b"release")

    sha256_file = write_sha256_file(output_zip)

    assert sha256_file == tmp_path / "release.sha256"
    assert sha256_file.read_text(encoding="ascii").endswith("  release.zip\n")


def test_release_zip_sha256_sidecar_creates_parent_directory(tmp_path: Path) -> None:
    output_zip = tmp_path / "release.zip"
    output_zip.write_bytes(b"release")
    sha256_file = tmp_path / "checksums" / "release.sha256"

    written = write_sha256_file(output_zip, sha256_file)

    assert written == sha256_file
    assert sha256_file.read_text(encoding="ascii").endswith("  release.zip\n")


def test_release_zip_uses_manifest_and_excludes_unlisted_files(tmp_path: Path) -> None:
    tracked = tmp_path / "src" / "app.py"
    tracked.parent.mkdir(parents=True)
    tracked.write_text("print('safe')", encoding="utf-8")
    secret = tmp_path / "credentials.txt"
    secret.write_text("TOP-SECRET-TOKEN=abc123", encoding="utf-8")
    manifest = tmp_path / "RELEASE_FILES.txt"
    manifest.write_text("RELEASE_FILES.txt\nsrc/app.py\n", encoding="utf-8")
    output_zip = tmp_path / "release.zip"

    build_release_zip(tmp_path, output_zip)

    with ZipFile(output_zip) as archive:
        names = archive.namelist()

    assert names == ["RELEASE_FILES.txt", "src/app.py"]
    assert "credentials.txt" not in names


def test_release_zip_denylist_filters_explicit_paths(tmp_path: Path) -> None:
    safe_file = tmp_path / "src" / "safe.py"
    env_file = tmp_path / ".env"
    db_file = tmp_path / "runtime.sqlite"
    node_file = tmp_path / "dashboard" / "node_modules" / "pkg" / "index.js"
    next_file = tmp_path / "dashboard" / ".next" / "server" / "page.js"
    tsbuild_file = tmp_path / "dashboard" / "tsconfig.tsbuildinfo"
    next_env_file = tmp_path / "dashboard" / "next-env.d.ts"
    log_file = tmp_path / "logs" / "runtime.log"
    lock_file = tmp_path / "okx_signal.lock"
    safe_file.parent.mkdir(parents=True)
    safe_file.write_text("print('safe')", encoding="utf-8")
    env_file.write_text("FEISHU_WEBHOOK_URL=secret", encoding="utf-8")
    db_file.write_text("sqlite", encoding="utf-8")
    lock_file.write_text("locked", encoding="utf-8")
    for generated in [node_file, next_file, tsbuild_file, next_env_file, log_file]:
        generated.parent.mkdir(parents=True, exist_ok=True)
        generated.write_text("generated", encoding="utf-8")
    output_zip = tmp_path / "release.zip"

    build_release_zip(
        tmp_path,
        output_zip,
        paths=[
            safe_file.relative_to(tmp_path),
            env_file.relative_to(tmp_path),
            db_file.relative_to(tmp_path),
            node_file.relative_to(tmp_path),
            next_file.relative_to(tmp_path),
            tsbuild_file.relative_to(tmp_path),
            next_env_file.relative_to(tmp_path),
            log_file.relative_to(tmp_path),
            lock_file.relative_to(tmp_path),
        ],
    )

    with ZipFile(output_zip) as archive:
        names = archive.namelist()

    assert names == ["src/safe.py"]


def test_release_zip_manifest_refuses_sensitive_paths(tmp_path: Path) -> None:
    safe_file = tmp_path / "src" / "safe.py"
    safe_file.parent.mkdir(parents=True)
    safe_file.write_text("print('safe')", encoding="utf-8")
    (tmp_path / ".env").write_text("FEISHU_WEBHOOK_URL=secret", encoding="utf-8")
    manifest = tmp_path / "RELEASE_FILES.txt"
    manifest.write_text("RELEASE_FILES.txt\nsrc/safe.py\n.env\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="unsafe release manifest entry"):
        build_release_zip(tmp_path, tmp_path / "release.zip")


def test_release_zip_uses_explicit_manifest(tmp_path: Path) -> None:
    safe_file = tmp_path / "src" / "safe.py"
    safe_file.parent.mkdir(parents=True)
    safe_file.write_text("print('safe')", encoding="utf-8")
    secret = tmp_path / "credentials.txt"
    secret.write_text("TOP-SECRET-TOKEN=abc123", encoding="utf-8")
    manifest = tmp_path / "RELEASE_FILES.txt"
    manifest.write_text("RELEASE_FILES.txt\nsrc/safe.py\n", encoding="utf-8")

    output_zip = tmp_path / "release.zip"

    build_release_zip(tmp_path, output_zip)

    with ZipFile(output_zip) as archive:
        names = archive.namelist()

    assert names == ["RELEASE_FILES.txt", "src/safe.py"]
    assert "credentials.txt" not in names


def test_release_zip_refuses_missing_manifest(tmp_path: Path) -> None:
    (tmp_path / "credentials.txt").write_text("TOP-SECRET-TOKEN=abc123", encoding="utf-8")

    with pytest.raises(RuntimeError, match="release manifest missing"):
        build_release_zip(tmp_path, tmp_path / "release.zip")


def test_release_docs_do_not_advertise_live_order_defaults() -> None:
    docs = "\n".join(
        [
            _read("README.md"),
            _read("docs/RELEASE_SAFETY.md"),
            _read("docs/SYSTEM_ARCHITECTURE.md"),
        ]
    )

    forbidden = [
        "live_order_enabled: true",
        "auto_close_enabled: true",
        "dry_run_enabled: false",
        "SIGNAL_ONLY=false",
        "DATA_READ_ONLY=false",
        "OKX_AUTO_CLOSE_ENABLED=true",
    ]
    forbidden.extend(PRIVATE_OKX_TOKENS)
    for token in forbidden:
        assert token not in docs


def test_release_docs_mark_learning_paths_experimental_not_production_tuning() -> None:
    docs = "\n".join(
        [
            _read("docs/RELEASE_SAFETY.md"),
            _read("docs/SYSTEM_ARCHITECTURE.md"),
        ]
    )

    assert "experimental sidecar paths" in docs
    assert "not production automatic tuning features" in docs
    assert "must not promote parameters into runtime by themselves" in docs
    assert "strict research acceptance flow and operator review" in docs


def test_ml_live_scoring_paths_are_observation_only() -> None:
    shadow_text = _read("src/okx_signal_system/ml/shadow_trading.py")
    regime_text = _read("src/okx_signal_system/ml/regime_adaptive.py")
    brain_text = _read("src/okx_signal_system/ml/trading_brain.py")

    assert "if _called_from_realtime_decision_path():" in shadow_text
    assert "return 0.0" in shadow_text
    assert "def offline_score_adjustment" in shadow_text
    assert "def offline_score_penalty" in regime_text
    assert "def offline_leverage_factor" in regime_text
    assert "self.live_param_updates_enabled = False" in brain_text


def test_release_facing_text_is_signal_only() -> None:
    text = _release_text()

    forbidden = [
        "\u6b63\u5f0f\u4ea4\u6613\u4fe1\u53f7",
        "\u5b9e\u76d8\u4e0b\u5355",
        "\u4e0b\u5355\u63d0\u9192",
        "live order",
        "open_positions:",
        "\u4ed3\u4f4d:",
        "\u6760\u6746:",
        "\u4fdd\u8bc1\u91d1\u6b62\u635f\u98ce\u9669",
        "\u6267\u884c\u6307\u4ee4",
        "okx.trade",
        "position_monitor",
        "place_order",
        "cancel_order",
        "get_positions",
        "get_account_balance",
    ]
    for token in forbidden:
        assert token not in text


def test_pyinstaller_datas_do_not_include_runtime_artifacts() -> None:
    spec = _read("okx_signal.spec")
    forbidden = [
        "('.env', '.')",
        '(".env", ".")',
        "build.log",
        "outputs",
        "output",
        ".pytest_cache",
        "__pycache__",
        ".sqlite",
        ".sqlite3",
        ".db",
    ]

    for token in forbidden:
        assert token not in spec


def test_realtime_runtime_api_does_not_expose_trade_execution() -> None:
    from okx_signal_system.exchange.realtime import OKXRealtimeAPI

    api = OKXRealtimeAPI.__new__(OKXRealtimeAPI)

    for name in ["place_order", "cancel_order", "get_positions", "get_account_balance", "close_position"]:
        assert not hasattr(api, name)
    assert hasattr(api, "get_market_data")
    assert hasattr(api, "get_candles")


def test_okx_release_adapter_is_public_market_data_only() -> None:
    source = "\n".join(
        [
            _read("src/okx_signal_system/exchange/okx.py"),
            _read("src/okx_signal_system/exchange/okx_public.py"),
            _read("okx_signal.spec"),
        ]
    )

    forbidden = [
        "OKX_SECRET_KEY",
        "OKX_API_KEY",
        "OKX_PASSPHRASE",
        "OK-ACCESS-KEY",
        "OK-ACCESS-SIGN",
        "hmac",
        "/api/v5/account/",
        "/api/v5/trade/",
        "place_order",
        "close_position",
        "get_open_orders",
        "get_account_balance",
        "get_account_positions",
        "okx.account",
        "okx.trade",
    ]
    for token in forbidden:
        assert token not in source
