from __future__ import annotations

import re
import tomllib
from pathlib import Path
from zipfile import ZipFile

import okx_signal_system
from scripts.build_release_zip import build_release_zip
from okx_signal_system.config import load_config


ROOT = Path(__file__).resolve().parents[1]
RELEASE_TEXT_FILES = [
    "README.md",
    "docs/RELEASE_SAFETY.md",
    "docs/SYSTEM_ARCHITECTURE.md",
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
    ".pytest_cache/",
    "__pycache__/",
    "*.py[cod]",
    "output/",
    "outputs/*",
    "*.sqlite",
    "*.sqlite3",
    "*.db",
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
    pyproject = tomllib.loads(_read("pyproject.toml"))
    package_version = okx_signal_system.__version__
    pkg_info = _read("src/okx_contract_signal_system.egg-info/PKG-INFO")
    main_text = _read("main.py")
    gui_text = _read("gui.py")
    start_text = _read("start.bat")

    assert pyproject["project"]["version"] == package_version
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


def test_env_example_is_signal_only_and_has_no_private_okx_keys() -> None:
    text = _read(".env.example")

    assert "SIGNAL_ONLY=true" in text
    assert "DATA_READ_ONLY=true" in text
    assert "FEISHU_ENABLED=true" in text
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

    for pattern in [".env .env.*", "*.py[cod]", "*.sqlite *.sqlite3 *.db", "build.log"]:
        assert pattern in manifest
    for directory in [".pytest_cache", "__pycache__", "output", "outputs"]:
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
        "output/** export-ignore",
        "outputs/** export-ignore",
        "*.pyc export-ignore",
        "*.pyo export-ignore",
        "*.sqlite export-ignore",
        "*.sqlite3 export-ignore",
        "*.db export-ignore",
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
