from okx_signal_system.config import load_config
from okx_signal_system.paths import find_lightweight_history


def test_base_config_locks_okx_and_disables_live_orders() -> None:
    cfg = load_config("base.yaml")
    assert cfg["project"]["exchange"] == "OKX"
    assert cfg["data"]["root_dir"] is None
    assert cfg["data"]["timeframe"] == "15m"
    assert cfg["data"]["trend_timeframe"] == "1h"
    assert cfg["execution"]["live_order_enabled"] is False
    assert cfg["execution"]["auto_close_enabled"] is False
    assert cfg["learning"]["live_param_updates_enabled"] is False


def test_find_history_uses_jiaoyi_data_dir_before_config(tmp_path, monkeypatch) -> None:
    env_root = tmp_path / "env_data"
    cfg_root = tmp_path / "cfg_data"
    dataset = "okx_15m_extended"
    expected = env_root / "lightweight_history" / dataset
    expected.mkdir(parents=True)
    (cfg_root / "lightweight_history" / dataset).mkdir(parents=True)

    monkeypatch.setenv("JIAOYI_DATA_DIR", str(env_root))
    monkeypatch.setattr(
        "okx_signal_system.paths._data_root_from_config",
        lambda: cfg_root,
    )

    assert find_lightweight_history(dataset) == expected


def test_find_history_uses_config_root_dir(tmp_path, monkeypatch) -> None:
    dataset = "okx_15m_extended"
    data_root = tmp_path / "data"
    expected = data_root / "lightweight_history" / dataset
    expected.mkdir(parents=True)
    config_dir = tmp_path / "project" / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "base.yaml").write_text(
        f"data:\n  root_dir: {data_root.as_posix()}\n",
        encoding="utf-8",
    )

    monkeypatch.delenv("JIAOYI_DATA_DIR", raising=False)
    monkeypatch.setattr(
        "okx_signal_system.paths.package_project_root",
        lambda start=None: tmp_path / "project",
    )

    assert find_lightweight_history(dataset) == expected
