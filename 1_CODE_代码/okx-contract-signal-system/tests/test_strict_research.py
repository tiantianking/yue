from okx_signal_system.backtest.evaluation import evaluate_symbol
from okx_signal_system.backtest.grid_search import parameter_grid, run_grid_search, select_best_params
from okx_signal_system.backtest.research import run_dataset_research, run_train_valid_symbol
from okx_signal_system.data.loader import load_symbol_file
from okx_signal_system.paths import find_lightweight_history
from okx_signal_system.strategy.trend_breakout import StrategyParams


def btc_frame(rows: int = 700):
    return load_symbol_file(find_lightweight_history("okx_1h_extended") / "BTC_USDT_USDT_1h.parquet").frame.head(rows)


def test_parameter_grid_has_432_combinations() -> None:
    assert len(parameter_grid()) == 432


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


def test_train_valid_symbol_returns_required_sections() -> None:
    result = run_train_valid_symbol(
        btc_frame(700),
        inst_id="BTC-USDT-SWAP",
        params_grid=[StrategyParams(fast_ema=10, slow_ema=50, breakout_window=20, max_hold_bars=24)],
    )
    assert {"grid_results", "train_summary", "valid_summary", "evaluation", "selected_params"}.issubset(result)


def test_dataset_research_outputs_symbol_result_table() -> None:
    table = run_dataset_research(
        max_symbols=1,
        params_grid=[StrategyParams(fast_ema=10, slow_ema=50, breakout_window=20, max_hold_bars=24)],
    )
    assert {"symbol", "valid_profit_factor", "pass_fail", "fail_reasons"}.issubset(table.columns)
