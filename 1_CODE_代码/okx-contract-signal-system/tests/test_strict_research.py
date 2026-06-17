import pytest
import pandas as pd

from tests._integration import require_lightweight_history
from okx_signal_system.backtest.evaluation import evaluate_symbol
from okx_signal_system.backtest.grid_search import parameter_grid, run_grid_search, select_best_params
from okx_signal_system.backtest.research import (
    BlindRegistry,
    NoValidParameterSetError,
    ResearchValidationConfig,
    build_data_manifest,
    build_acceptance_checklist,
    common_calendar_split,
    replay_cost_stress,
    run_dataset_research,
    run_dataset_research_artifacts,
    run_shared_train_grid,
    run_walk_forward_validation,
    run_train_valid_symbol,
    select_shared_params,
    write_research_artifacts,
)
from okx_signal_system.data.loader import SymbolData
from okx_signal_system.data.loader import load_symbol_file
from okx_signal_system.risk.costs import CostConfig
from okx_signal_system.strategy.trend_breakout import StrategyParams


def btc_frame(rows: int = 700):
    history = require_lightweight_history("okx_1h_extended", "BTC_USDT_USDT_1h.parquet")
    return load_symbol_file(history / "BTC_USDT_USDT_1h.parquet").frame.head(rows)


def test_parameter_grid_has_1296_combinations() -> None:
    assert len(parameter_grid()) == 216


@pytest.mark.integration
def test_grid_search_selects_params() -> None:
    grid = run_grid_search(
        btc_frame(500),
        inst_id="BTC-USDT-SWAP",
        params_grid=[StrategyParams(fast_ema=10, slow_ema=50, breakout_window=20, max_hold_bars=24)],
    )
    selected = select_best_params(grid)
    assert selected.fast_ema == 10


def test_evaluation_flags_failed_validation() -> None:
    result = evaluate_symbol(
        {"total_return": 0.1, "profit_factor": 1.2, "payoff_ratio": 1.4, "max_drawdown": 0.1, "total_trades": 30, "hit_27pct_stop": 0, "pnl_share_from_gt5x": 0.0},
        {"total_return": -0.1, "profit_factor": 0.9, "payoff_ratio": 1.0, "max_drawdown": 0.2, "total_trades": 5, "hit_27pct_stop": 1, "pnl_share_from_gt5x": 0.5},
    )
    assert result["pass_fail"] == "failed"
    assert "valid_profit_factor_below_1_05" in result["reasons"]


def _research_frame(rows: int, *, start: str = "2026-01-01T00:00:00Z") -> pd.DataFrame:
    ts = pd.date_range(start, periods=rows, freq="15min", tz="UTC")
    return pd.DataFrame(
        {
            "ts": ts,
            "open": [100.0] * rows,
            "high": [101.0] * rows,
            "low": [99.0] * rows,
            "close": [100.5] * rows,
            "volume": [1000.0] * rows,
            "is_closed": [True] * rows,
            "timeframe": ["15m"] * rows,
        }
    )


def test_common_calendar_split_uses_shared_dates_and_gap_buffers(tmp_path) -> None:
    config = ResearchValidationConfig(
        train_fraction=0.50,
        validation_fraction=0.25,
        purge_bars=2,
        embargo_bars=1,
    )
    params = [StrategyParams(fast_ema=2, slow_ema=3, breakout_window=4, max_hold_bars=5)]
    symbols = [
        SymbolData("BTC-USDT-SWAP", tmp_path / "btc.parquet", _research_frame(80)),
        SymbolData("ETH-USDT-SWAP", tmp_path / "eth.parquet", _research_frame(70, start="2026-01-01T02:30:00Z")),
    ]

    splits = common_calendar_split(symbols, params_grid=params, signal_timeframe="15m", config=config)

    btc = splits["BTC-USDT-SWAP"]
    eth = splits["ETH-USDT-SWAP"]
    assert btc.boundaries["common_start"] == eth.boundaries["common_start"]
    assert btc.train["ts"].max() < btc.validation["ts"].min()
    assert btc.validation["ts"].max() < btc.blind["ts"].min()
    assert len(btc.validation) > 0
    assert len(btc.blind) > 0


def test_common_calendar_split_keeps_boundaries_when_symbol_has_missing_bars(tmp_path) -> None:
    config = ResearchValidationConfig(
        train_fraction=0.50,
        validation_fraction=0.25,
        purge_bars=2,
        embargo_bars=1,
    )
    params = [StrategyParams(fast_ema=2, slow_ema=3, breakout_window=4, max_hold_bars=5)]
    eth = _research_frame(90).drop(index=[30, 31, 32, 33, 34, 35]).reset_index(drop=True)
    symbols = [
        SymbolData("BTC-USDT-SWAP", tmp_path / "btc.parquet", _research_frame(90)),
        SymbolData("ETH-USDT-SWAP", tmp_path / "eth.parquet", eth),
    ]

    splits = common_calendar_split(symbols, params_grid=params, signal_timeframe="15m", config=config)

    btc = splits["BTC-USDT-SWAP"].boundaries
    eth = splits["ETH-USDT-SWAP"].boundaries
    assert btc["train_end"] == eth["train_end"]
    assert btc["validation_start"] == eth["validation_start"]
    assert btc["blind_start"] == eth["blind_start"]


def test_common_calendar_split_evaluation_windows_include_warmup_history(tmp_path) -> None:
    config = ResearchValidationConfig(
        train_fraction=0.50,
        validation_fraction=0.25,
        purge_bars=2,
        embargo_bars=1,
    )
    params = [StrategyParams(fast_ema=2, slow_ema=3, breakout_window=4, max_hold_bars=5)]
    symbols = [
        SymbolData("BTC-USDT-SWAP", tmp_path / "btc.parquet", _research_frame(90)),
        SymbolData("ETH-USDT-SWAP", tmp_path / "eth.parquet", _research_frame(90)),
    ]

    split = common_calendar_split(symbols, params_grid=params, signal_timeframe="15m", config=config)["BTC-USDT-SWAP"]

    assert split.validation_window.trade_start == split.boundaries["validation_start"]
    assert split.validation_window.trade_end == split.boundaries["validation_end"]
    assert split.validation_window.frame_with_warmup["ts"].min() < split.validation["ts"].min()
    assert split.blind_window.frame_with_warmup["ts"].min() < split.blind["ts"].min()


def test_common_calendar_split_fails_when_strict_windows_are_empty(tmp_path) -> None:
    config = ResearchValidationConfig(
        train_fraction=0.60,
        validation_fraction=0.25,
        purge_bars=10,
        embargo_bars=5,
    )
    symbols = [
        SymbolData("BTC-USDT-SWAP", tmp_path / "btc.parquet", _research_frame(20)),
        SymbolData("ETH-USDT-SWAP", tmp_path / "eth.parquet", _research_frame(20)),
    ]

    with pytest.raises(ValueError, match="STRICT_SPLIT_UNAVAILABLE"):
        common_calendar_split(
            symbols,
            params_grid=[StrategyParams(fast_ema=2, slow_ema=3, breakout_window=4, max_hold_bars=5)],
            signal_timeframe="15m",
            config=config,
        )


def test_data_manifest_hash_changes_when_ohlcv_content_changes(tmp_path) -> None:
    base = _research_frame(20)
    changed = base.copy()
    changed.loc[5, "close"] = 123.45
    first = build_data_manifest("unit", [SymbolData("BTC-USDT-SWAP", tmp_path / "btc.parquet", base)])
    second = build_data_manifest("unit", [SymbolData("BTC-USDT-SWAP", tmp_path / "btc.parquet", changed)])

    assert first["symbols"]["BTC-USDT-SWAP"]["content_sha256"] != second["symbols"]["BTC-USDT-SWAP"]["content_sha256"]
    assert first["manifest_hash"] != second["manifest_hash"]


def test_blind_registry_allows_one_open_then_rejects_same_research_id(tmp_path) -> None:
    registry = BlindRegistry(tmp_path / "blind_registry.sqlite3")
    manifest = {
        "registry_id": "research-1",
        "dataset_hash": "dataset",
        "config_hash": "config",
        "params_hash": "params",
        "git_commit": "commit",
    }

    assert registry.open_once(manifest) == "research-1"
    registry.seal("research-1", {"blind_total_trades": 1})
    with pytest.raises(RuntimeError, match="BLIND_ALREADY_OPENED"):
        registry.open_once(manifest)


def test_shared_param_selection_rejects_infinite_profit_factor() -> None:
    grid = pd.DataFrame(
        [
            {
                "fast_ema": 1,
                "slow_ema": 2,
                "breakout_window": 3,
                "atr_stop_mult": 1.0,
                "take_profit_mult": 2.0,
                "max_hold_bars": 5,
                "atr_window": 14,
                "train_profit_factor": float("inf"),
                "train_win_rate": 1.0,
                "train_total_return": 1.0,
                "train_total_trades": 200,
                "train_max_drawdown": 0.01,
                "centrality_distance": 0.0,
                "stable_neighbor_count": 1,
                "passed_train_gate": True,
            }
        ]
    )

    with pytest.raises(NoValidParameterSetError, match="NO_FINITE_PROFIT_FACTOR"):
        select_shared_params(grid)


def test_neighbor_stability_requires_count_and_ratio() -> None:
    params = {
        "fast_ema": 10,
        "slow_ema": 50,
        "breakout_window": 20,
        "atr_stop_mult": 2.0,
        "take_profit_mult": 4.0,
        "max_hold_bars": 24,
        "atr_window": 14,
        "train_profit_factor": 1.5,
        "train_win_rate": 0.55,
        "train_total_return": 0.08,
        "train_total_trades": 120,
        "train_max_drawdown": 0.05,
        "centrality_distance": 0.0,
        "base_train_gate": True,
        "stable_neighbor_count": 3,
        "stable_neighbor_ratio": 0.49,
        "passed_train_gate": False,
    }
    grid = pd.DataFrame([params])

    with pytest.raises(NoValidParameterSetError, match="NO_VALID_PARAMETER_SET"):
        select_shared_params(grid)


def test_replay_cost_stress_outputs_three_scenarios() -> None:
    trades = pd.DataFrame(
        [
            {
                "inst_id": "BTC-USDT-SWAP",
                "entry_time": "2026-01-01T00:00:00Z",
                "exit_time": "2026-01-01T09:00:00Z",
                "side": "long",
                "entry_price": 100.0,
                "exit_price": 112.0,
                "qty": 10.0,
                "gross_pnl": 120.0,
                "costs": 10.0,
                "net_pnl": 110.0,
                "risk_amount": 100.0,
                "net_r": 1.1,
                "final_net_r": 1.1,
                "leverage_used": 1.0,
                "near_liq_flag": False,
                "market_regime": "high_vol_trend",
            },
            {
                "inst_id": "ETH-USDT-SWAP",
                "entry_time": "2026-01-01T02:00:00Z",
                "exit_time": "2026-01-01T11:00:00Z",
                "side": "short",
                "entry_price": 100.0,
                "exit_price": 106.0,
                "qty": 10.0,
                "gross_pnl": -60.0,
                "costs": 10.0,
                "net_pnl": -70.0,
                "risk_amount": 100.0,
                "net_r": -0.7,
                "final_net_r": -0.7,
                "leverage_used": 1.0,
                "near_liq_flag": False,
                "market_regime": "low_vol_range",
            },
        ]
    )

    stress = replay_cost_stress(trades, cost_config=CostConfig(funding_rate=0.0002))

    assert stress["scenario"].tolist() == ["baseline", "stress_1_5x", "stress_2x"]
    assert {
        "net_r",
        "profit_factor",
        "max_drawdown",
        "long_trades",
        "short_trades",
        "top_symbol",
        "top_regime",
        "funding_fee",
        "recompute_source",
    }.issubset(stress.columns)
    assert stress["recompute_source"].eq("trade_fact_recompute").all()
    assert stress.loc[stress["scenario"] == "stress_2x", "net_r"].iloc[0] < stress.loc[stress["scenario"] == "baseline", "net_r"].iloc[0]
    assert stress.loc[stress["scenario"] == "stress_2x", "funding_fee"].iloc[0] > stress.loc[stress["scenario"] == "baseline", "funding_fee"].iloc[0]


def test_acceptance_checklist_requires_real_blind_lock_and_walk_forward() -> None:
    train_grid = pd.DataFrame(
        [
            {
                "finite_profit_factor": True,
                "stable_neighbor_count": 3,
                "stable_neighbor_ratio": 0.5,
                "passed_train_gate": True,
            }
        ]
    )
    portfolio = pd.DataFrame(
        [
            {
                "valid_total_trades": 100,
                "blind_total_trades": 0,
            }
        ]
    )

    checklist = build_acceptance_checklist(
        train_grid_results=train_grid,
        selected=StrategyParams(),
        selected_params_available=True,
        validation_results=pd.DataFrame([{"symbol": "BTC-USDT-SWAP"}]),
        portfolio_results=portfolio,
        leverage_risk=pd.DataFrame([{"near_liq_flag": False}]),
        cost_stress=pd.DataFrame(
            [
                {"scenario": "baseline", "recompute_source": "trade_fact_recompute"},
                {"scenario": "stress_1_5x", "recompute_source": "trade_fact_recompute"},
                {"scenario": "stress_2x", "recompute_source": "trade_fact_recompute"},
            ]
        ),
        walk_forward_results={"window_count": 2, "valid_pf_pass_ratio": 0.5, "valid_pf_median": 1.1, "purge_bars": 2, "embargo_bars": 1},
        shared_params=True,
        split_status="strict",
        blind_lock_status="unlocked",
    )

    blind_check = checklist.set_index("check").loc["blind_set_locked"]
    assert bool(blind_check["passed"]) is False


def test_shared_train_grid_uses_portfolio_profit_factor(monkeypatch, tmp_path) -> None:
    rows = [
        {
            "fast_ema": 10,
            "slow_ema": 50,
            "breakout_window": 20,
            "atr_stop_mult": 2.0,
            "take_profit_mult": 4.0,
            "max_hold_bars": 24,
            "atr_window": 14,
            "train_total_return": 0.10,
            "train_profit_factor": 10.0,
            "train_payoff_ratio": 2.0,
            "train_win_rate": 0.6,
            "train_total_trades": 100,
            "train_net_pnl": 100.0,
            "train_winning_net_pnl": 100.0,
            "train_losing_net_pnl": -10.0,
            "train_max_drawdown": 0.02,
            "train_avg_hold_hours": 1.0,
            "train_hit_27pct_stop": 0,
            "train_near_liq_trades": 0,
            "train_gt5x_trade_pct": 0.0,
            "train_pnl_share_from_gt5x": 0.0,
        },
        {
            "fast_ema": 10,
            "slow_ema": 50,
            "breakout_window": 20,
            "atr_stop_mult": 2.0,
            "take_profit_mult": 4.0,
            "max_hold_bars": 24,
            "atr_window": 14,
            "train_total_return": -0.90,
            "train_profit_factor": 0.1,
            "train_payoff_ratio": 2.0,
            "train_win_rate": 0.4,
            "train_total_trades": 100,
            "train_net_pnl": -900.0,
            "train_winning_net_pnl": 100.0,
            "train_losing_net_pnl": -1000.0,
            "train_max_drawdown": 0.02,
            "train_avg_hold_hours": 1.0,
            "train_hit_27pct_stop": 0,
            "train_near_liq_trades": 0,
            "train_gt5x_trade_pct": 0.0,
            "train_pnl_share_from_gt5x": 0.0,
        },
    ]

    def fake_grid(_frame, *, inst_id, **_kwargs):
        row = rows[0] if inst_id.startswith("BTC") else rows[1]
        return pd.DataFrame([row])

    monkeypatch.setattr("okx_signal_system.backtest.research.run_grid_search", fake_grid)
    symbols = [
        SymbolData("BTC-USDT-SWAP", tmp_path / "btc.parquet", _research_frame(30)),
        SymbolData("ETH-USDT-SWAP", tmp_path / "eth.parquet", _research_frame(30)),
    ]

    grid = run_shared_train_grid(
        symbols,
        params_grid=[StrategyParams(fast_ema=10, slow_ema=50, breakout_window=20, atr_stop_mult=2.0, take_profit_mult=4.0, max_hold_bars=24)],
        validation_config=ResearchValidationConfig(min_train_trades=1),
    )

    assert grid["train_profit_factor"].iloc[0] == pytest.approx(200.0 / 1010.0)
    assert grid["median_symbol_pf"].iloc[0] == pytest.approx(5.05)


def test_write_research_artifacts_rejects_empty_sample_trades(tmp_path) -> None:
    artifacts = {
        "sample_trades": pd.DataFrame(),
        "portfolio_results": pd.DataFrame(
            [
                {
                    "valid_total_trades": 0,
                    "valid_total_return": 0.0,
                    "valid_profit_factor": 0.0,
                    "pass_fail": "failed",
                }
            ]
        ),
    }

    with pytest.raises(ValueError, match="research sample_trades"):
        write_research_artifacts(artifacts, tmp_path)


@pytest.mark.integration
def test_train_valid_symbol_returns_required_sections() -> None:
    result = run_train_valid_symbol(
        btc_frame(700),
        inst_id="BTC-USDT-SWAP",
        params_grid=[StrategyParams(fast_ema=10, slow_ema=50, breakout_window=20, max_hold_bars=24)],
    )
    assert {"grid_results", "train_summary", "valid_summary", "evaluation", "selected_params"}.issubset(result)


@pytest.mark.integration
def test_dataset_research_outputs_symbol_result_table() -> None:
    require_lightweight_history("okx_15m_extended", min_parquet_files=1)
    table = run_dataset_research(
        max_symbols=1,
        params_grid=[StrategyParams(fast_ema=10, slow_ema=50, breakout_window=20, max_hold_bars=24)],
    )
    assert {"symbol", "valid_profit_factor", "pass_fail", "fail_reasons"}.issubset(table.columns)
    assert table["shared_params"].all()
    assert table["fail_reasons"].eq("NO_VALID_PARAMETER_SET").all()


@pytest.mark.integration
def test_shared_research_artifacts_use_one_param_set(tmp_path) -> None:
    require_lightweight_history("okx_15m_extended", min_parquet_files=2)
    grid = [
        StrategyParams(fast_ema=10, slow_ema=50, breakout_window=20, max_hold_bars=24),
        StrategyParams(fast_ema=20, slow_ema=60, breakout_window=40, max_hold_bars=48),
    ]
    artifacts = run_dataset_research_artifacts(max_symbols=2, params_grid=grid)
    single = artifacts["single_symbol_results"]
    assert artifacts["selected_params"] == {}
    assert single["fail_reasons"].eq("NO_VALID_PARAMETER_SET").all()
    assert {"train_grid_results", "selected_params", "validation_results", "portfolio_results", "leverage_risk", "acceptance_checklist"}.issubset(artifacts)
    with pytest.raises(ValueError, match="research sample_trades"):
        write_research_artifacts(artifacts, tmp_path)


@pytest.mark.integration
def test_shared_param_selection_refuses_failed_gate() -> None:
    grid = run_grid_search(
        btc_frame(500),
        inst_id="BTC-USDT-SWAP",
        params_grid=[StrategyParams(fast_ema=10, slow_ema=50, breakout_window=20, max_hold_bars=24)],
    )
    grid["passed_train_gate"] = False
    grid["profitable_symbol_ratio"] = 0.0
    grid["centrality_distance"] = 0.0
    with pytest.raises(NoValidParameterSetError, match="NO_VALID_PARAMETER_SET"):
        select_shared_params(grid)
