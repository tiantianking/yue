import json

import pandas as pd
import pytest

from okx_signal_system.config import RuntimeConfig, load_config, load_runtime_config, write_effective_config
from okx_signal_system.risk.costs import estimate_costs, research_position_size
from okx_signal_system.strategy.trend_breakout import TradeSignal
from okx_signal_system.paths import find_lightweight_history, find_runtime_cache_root
from okx_signal_system.risk.model import estimated_liquidation_buffer_pct, validate_signal


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


def test_find_runtime_cache_uses_config_runtime_cache_root(tmp_path, monkeypatch) -> None:
    dataset = "okx_15m_extended"
    cache_root = tmp_path / "runtime_cache"
    expected = cache_root / "lightweight_history" / dataset
    config_dir = tmp_path / "project" / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "base.yaml").write_text(
        f"data:\n  runtime_cache_root: {cache_root.as_posix()}\n",
        encoding="utf-8",
    )

    monkeypatch.delenv("JIAOYI_RUNTIME_CACHE_DIR", raising=False)
    monkeypatch.setattr(
        "okx_signal_system.paths.package_project_root",
        lambda start=None: tmp_path / "project",
    )

    assert find_runtime_cache_root(dataset) == expected
    assert expected.is_dir()


def test_runtime_config_merges_declared_config_files() -> None:
    runtime_config = load_runtime_config()

    assert runtime_config.base["project"]["exchange"] == "OKX"
    assert "risk" in runtime_config.risk
    assert "fees" in runtime_config.fees
    assert len(runtime_config.sha256) == 64
    assert runtime_config.risk_config().initial_equity == 10000
    assert runtime_config.risk_config().max_leverage == 10
    assert runtime_config.cost_config().normal_slippage_bps == 5
    assert runtime_config.cost_config().funding_interval_hours == 8


def test_runtime_config_maps_risk_and_cost_fields() -> None:
    runtime_config = RuntimeConfig(
        base={},
        risk={
            "risk": {
                "per_symbol_initial_equity": 25000,
                "halt_equity_ratio": 0.66,
                "max_leverage": 7,
                "single_position_loss_pct": 0.19,
                "risk_per_trade_pct": 0.025,
                "margin_mode": "cross",
                "position_mode": "hedge",
                "maintenance_margin_rate": 0.007,
                "liquidation_cost_buffer_pct": 0.003,
                "min_stop_distance_pct": 0.011,
                "min_take_profit_distance_pct": 0.044,
                "min_rr": 4.2,
                "min_score": 8.1,
            }
        },
        fees={
            "fees": {
                "taker_fee_rate": 0.0007,
                "maker_fee_rate": 0.0003,
                "default_use_taker": False,
            },
            "slippage": {
                "normal_bps": 6,
                "stress_bps": 18,
                "participation_tiers": [{"max_rate": 0.002, "bps_add": 1}],
            },
            "funding": {
                "baseline_rate": 0.0002,
                "baseline_hours": 4,
                "stress_rates": [{"rate": 0.0004, "hours": 2}],
            },
        },
        sha256="x" * 64,
    )

    risk_config = runtime_config.risk_config()
    assert risk_config.initial_equity == 25000
    assert risk_config.halt_equity_ratio == 0.66
    assert risk_config.max_leverage == 7
    assert risk_config.single_position_loss_pct == 0.19
    assert risk_config.risk_per_trade_pct == 0.025
    assert risk_config.margin_mode == "cross"
    assert risk_config.position_mode == "hedge"
    assert risk_config.maintenance_margin_rate == 0.007
    assert risk_config.liquidation_cost_buffer_pct == 0.003
    assert risk_config.min_stop_distance_pct == 0.011
    assert risk_config.min_take_profit_distance_pct == 0.044
    assert risk_config.min_reward_to_risk == 4.2
    assert risk_config.min_signal_score == 8.1

    cost_config = runtime_config.cost_config()
    assert cost_config.taker_fee_rate == 0.0007
    assert cost_config.maker_fee_rate == 0.0003
    assert cost_config.default_use_taker is False
    assert cost_config.normal_slippage_bps == 6
    assert cost_config.stress_slippage_bps == 18
    assert cost_config.participation_tiers == ({"max_rate": 0.002, "bps_add": 1},)
    assert cost_config.funding_rate == 0.0002
    assert cost_config.funding_interval_hours == 4
    assert cost_config.stress_funding_rates == ({"rate": 0.0004, "hours": 2},)


def test_runtime_defaults_load_current_config(monkeypatch) -> None:
    runtime_config = RuntimeConfig(
        base={},
        risk={
            "risk": {
                "per_symbol_initial_equity": 50000,
                "risk_per_trade_pct": 0.02,
                "maintenance_margin_rate": 0.01,
                "min_score": 9.0,
            }
        },
        fees={"fees": {"taker_fee_rate": 0.001}, "slippage": {"normal_bps": 12}, "funding": {"baseline_rate": 0}},
        sha256="y" * 64,
    )

    monkeypatch.setattr("okx_signal_system.config.load_runtime_config", lambda: runtime_config)
    qty, risk_unit, notional = research_position_size(entry_price=100, stop_distance=10)
    assert qty == 100
    assert risk_unit == 1000
    assert notional == 10000
    assert estimated_liquidation_buffer_pct(10) == pytest.approx(0.088)

    costs = estimate_costs(
        entry_price=100,
        exit_price=110,
        qty=1,
        entry_time=pd.Timestamp("2026-01-01T00:00:00Z"),
        exit_time=pd.Timestamp("2026-01-01T01:00:00Z"),
    )
    assert costs.entry_fee == 0.1
    assert costs.exit_fee == 0.11
    assert costs.slippage_cost == 0.252

    signal = TradeSignal(
        ts=pd.Timestamp("2026-01-01T00:00:00Z"),
        inst_id="BTC-USDT-SWAP",
        side="long",
        entry_ref=100,
        stop_loss=95,
        take_profit=125,
        max_hold_bars=10,
        reason_codes=("test",),
        signal_score=8.5,
    )
    assert validate_signal(signal).reason == "signal_score_below_threshold"


def test_effective_config_writes_hash_and_inputs(tmp_path) -> None:
    path = write_effective_config(tmp_path)

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["base"]["project"]["mode"] == "SIGNAL_ONLY"
    assert "risk" in payload
    assert "fees" in payload
    assert len(payload["sha256"]) == 64
