from __future__ import annotations

from pathlib import Path

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


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _release_text() -> str:
    return "\n".join(_read(path) for path in RELEASE_TEXT_FILES)


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
