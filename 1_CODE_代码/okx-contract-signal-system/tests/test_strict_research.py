import pytest
import pandas as pd

from tests._integration import require_lightweight_history
from okx_signal_system.backtest.evaluation import evaluate_symbol
from okx_signal_system.backtest.grid_search import parameter_grid, run_grid_search, select_best_params
from okx_signal_system.backtest.research import (
    NoValidParameterSetError,
    run_dataset_research,
    run_dataset_research_artifacts,
    run_train_valid_symbol,
    select_shared_params,
    write_research_artifacts,
)
from okx_signal_system.data.loader import load_symbol_file
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
