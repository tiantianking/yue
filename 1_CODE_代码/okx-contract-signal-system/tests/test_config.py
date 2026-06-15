from okx_signal_system.config import load_config


def test_base_config_locks_okx_and_disables_live_orders() -> None:
    cfg = load_config("base.yaml")
    assert cfg["project"]["exchange"] == "OKX"
    assert cfg["data"]["timeframe"] == "15m"
    assert cfg["data"]["trend_timeframe"] == "1h"
    assert cfg["execution"]["live_order_enabled"] is False
    assert cfg["learning"]["live_param_updates_enabled"] is False
