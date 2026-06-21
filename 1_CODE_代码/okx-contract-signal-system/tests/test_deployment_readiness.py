from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from okx_signal_system.config import env_bool, feishu_notifications_enabled

ROOT = Path(__file__).resolve().parents[1]


def _load_script(name: str):
    path = ROOT / "scripts" / name
    spec = importlib.util.spec_from_file_location(name.replace(".py", ""), path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_feishu_environment_switch_overrides_yaml(monkeypatch) -> None:
    monkeypatch.setenv("FEISHU_ENABLED", "false")
    assert feishu_notifications_enabled(True) is False
    monkeypatch.setenv("FEISHU_ENABLED", "true")
    assert feishu_notifications_enabled(False) is True
    monkeypatch.setenv("FEISHU_ENABLED", "invalid-value")
    assert feishu_notifications_enabled(True) is False
    monkeypatch.delenv("FEISHU_ENABLED")
    assert feishu_notifications_enabled(False) is False


def test_environment_boolean_parser_is_fail_safe(monkeypatch) -> None:
    monkeypatch.setenv("SIGNAL_ONLY", "off")
    assert env_bool("SIGNAL_ONLY", True) is False
    monkeypatch.setenv("SIGNAL_ONLY", "unexpected")
    assert env_bool("SIGNAL_ONLY", True) is True


def test_feishu_send_text_respects_emergency_disable(monkeypatch) -> None:
    from okx_signal_system.notify import feishu

    monkeypatch.setenv("FEISHU_ENABLED", "false")
    monkeypatch.setattr(feishu, "FEISHU_WEBHOOK_URL", "https://example.invalid/webhook")
    assert feishu.send_text("must not be sent") is False


def test_disabled_feishu_does_not_enqueue_or_fail_outbox(monkeypatch, tmp_path) -> None:
    from okx_signal_system.signal_quality import LifecycleOutboxWorker, SignalLifecycleStore

    store = SignalLifecycleStore(tmp_path / "lifecycle.sqlite3")
    monkeypatch.setenv("FEISHU_ENABLED", "false")
    assert store.enqueue_notification(
        "suppressed",
        signal_id=None,
        event_type="STATUS_REPORT",
        payload={"status": "running"},
    ) is False
    assert store.pending_notifications() == []

    monkeypatch.setenv("FEISHU_ENABLED", "true")
    assert store.enqueue_notification(
        "existing",
        signal_id=None,
        event_type="STATUS_REPORT",
        payload={"status": "running"},
    ) is True
    monkeypatch.setenv("FEISHU_ENABLED", "false")

    class FailingDispatcher:
        def send_lifecycle_event(self, _item):
            raise AssertionError("dispatcher must not run while notifications are disabled")

    summary = LifecycleOutboxWorker(store, FailingDispatcher()).run_once()
    assert summary == {"sent": 0, "failed": 0}
    assert [item["outbox_id"] for item in store.pending_notifications()] == ["existing"]


def test_scheduler_module_has_cli_entrypoint() -> None:
    text = (ROOT / "src" / "okx_signal_system" / "scheduler.py").read_text(encoding="utf-8")
    assert 'if __name__ == "__main__":' in text
    assert "run_live_scan()" in text


def test_preflight_production_requires_notifications_and_manifest(monkeypatch, tmp_path) -> None:
    module = _load_script("preflight_check.py")
    monkeypatch.setenv("SIGNAL_ONLY", "true")
    monkeypatch.setenv("DATA_READ_ONLY", "true")
    monkeypatch.setenv("OKX_AUTO_CLOSE_ENABLED", "false")
    monkeypatch.setenv("FEISHU_ENABLED", "false")
    monkeypatch.delenv("FEISHU_WEBHOOK_URL", raising=False)
    for name in ("OKX_API_KEY", "OKX_SECRET_KEY", "OKX_PASSPHRASE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(
        module,
        "load_approved_manifest_status",
        lambda: SimpleNamespace(ok=False, reason="runtime_manifest_missing"),
    )

    observation = module.run_preflight("observation", tmp_path / "missing.env")
    production = module.run_preflight("production", tmp_path / "missing.env")

    assert not [item for item in observation if item.blocking and not item.ok]
    assert any(item.name == "feishu_configuration" and not item.ok for item in production)
    assert any(item.name == "approved_manifest" and not item.ok for item in production)


def test_healthcheck_observation_and_production_modes() -> None:
    module = _load_script("runtime_healthcheck.py")
    status = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "running",
        "error": None,
        "push_allowed": False,
        "websocket": {"connected": True, "degraded": False},
        "modules": {"closed_kline_backfill": {"all_complete": True}},
        "manifest_status": {"ok": False, "reason": "runtime_manifest_missing"},
        "lifecycle_summary": {"outbox": {"dead_letter": 0}},
    }
    observation = module.evaluate(status, mode="observation", max_age_seconds=1200)
    production = module.evaluate(status, mode="production", max_age_seconds=1200)
    assert all(item.ok for item in observation)
    assert any(item.name == "formal_push_allowed" and not item.ok for item in production)
    assert any(item.name == "approved_manifest_valid" and not item.ok for item in production)

    status_without_embedded_backfill = dict(status)
    status_without_embedded_backfill.pop("modules")
    fallback = module.evaluate(
        status_without_embedded_backfill,
        mode="observation",
        max_age_seconds=1200,
        fallback_backfill={"all_complete": True},
    )
    assert all(item.ok for item in fallback)


def test_requirements_lock_is_utf8_for_linux_pip() -> None:
    text = (ROOT / "requirements.lock").read_text(encoding="utf-8")
    assert "\x00" not in text
    assert "numpy==" in text
    assert "pandas==" in text
    assert "websocket-client==" in text
    assert "pytest==" not in text


def test_linux_deployment_assets_are_signal_only() -> None:
    service = (ROOT / "deployment" / "systemd" / "okx-signal.service").read_text(encoding="utf-8")
    env_example = (ROOT / "deployment" / "okx-signal.env.example").read_text(encoding="utf-8")
    installer = (ROOT / "deployment" / "install_linux.sh").read_text(encoding="utf-8")

    assert "main.py --cli" in service
    assert "Restart=always" in service
    assert "scripts/preflight_check.py" in service
    assert "SIGNAL_ONLY=true" in env_example
    assert "DATA_READ_ONLY=true" in env_example
    assert "OKX_AUTO_CLOSE_ENABLED=false" in env_example
    assert "OKX_API_KEY=" in env_example
    assert "systemctl enable okx-signal.service okx-signal-health.timer" in installer
    assert 'RELEASE_MANIFEST="${SOURCE_DIR}/RELEASE_FILES.txt"' in installer
    assert 'cp -a "${SOURCE_DIR}/."' not in installer
