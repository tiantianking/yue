from okx_signal_system.backtest.runner import run_backtest, split_train_valid, summarize_trades
from okx_signal_system.data.loader import load_symbol_file
from okx_signal_system.paths import find_lightweight_history


def btc_frame():
    return load_symbol_file(find_lightweight_history("okx_1h_extended") / "BTC_USDT_USDT_1h.parquet").frame.head(1000)


def test_train_valid_split_preserves_order() -> None:
    train, valid = split_train_valid(btc_frame(), valid_fraction=0.25)
    assert len(train) > len(valid)
    assert train["ts"].max() < valid["ts"].min()


def test_run_backtest_returns_trade_table() -> None:
    trades = run_backtest(btc_frame(), inst_id="BTC-USDT-SWAP")
    assert {"entry_time", "exit_time", "side", "net_pnl", "costs", "exit_reason"}.issubset(trades.columns)


def test_summarize_trades_has_required_metrics() -> None:
    trades = run_backtest(btc_frame(), inst_id="BTC-USDT-SWAP")
    summary = summarize_trades(trades)
    assert {"total_return", "profit_factor", "payoff_ratio", "max_drawdown", "win_rate", "total_trades", "status"}.issubset(summary)
