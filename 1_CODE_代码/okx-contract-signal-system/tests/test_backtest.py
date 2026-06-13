import pandas as pd

from okx_signal_system.backtest.runner import (
    run_backtest,
    signal_candidate_indices,
    split_train_valid,
    summarize_trades,
)
from okx_signal_system.data.loader import load_symbol_file
from okx_signal_system.features.indicators import build_feature_frame
from okx_signal_system.paths import find_lightweight_history
from okx_signal_system.strategy.trend_breakout import StrategyParams, build_signal


def btc_frame():
    return load_symbol_file(find_lightweight_history("okx_1h_extended") / "BTC_USDT_USDT_1h.parquet").frame.head(1000)


def ada_frame():
    return load_symbol_file(find_lightweight_history("okx_1h_extended") / "ADA_USDT_USDT_1h.parquet").frame.tail(2500)


def sample_params() -> StrategyParams:
    return StrategyParams(
        fast_ema=10,
        slow_ema=80,
        breakout_window=60,
        atr_stop_mult=1.5,
        take_profit_mult=3.5,
        max_hold_bars=24,
    )


def test_train_valid_split_preserves_order() -> None:
    train, valid = split_train_valid(btc_frame(), valid_fraction=0.25)
    assert len(train) > len(valid)
    assert train["ts"].max() < valid["ts"].min()


def test_run_backtest_returns_trade_table() -> None:
    trades = run_backtest(btc_frame(), inst_id="BTC-USDT-SWAP", params=sample_params())
    assert {"entry_time", "exit_time", "side", "net_pnl", "costs", "exit_reason"}.issubset(trades.columns)


def test_summarize_trades_has_required_metrics() -> None:
    trades = run_backtest(btc_frame(), inst_id="BTC-USDT-SWAP", params=sample_params())
    summary = summarize_trades(trades)
    assert {"total_return", "profit_factor", "payoff_ratio", "max_drawdown", "win_rate", "total_trades", "status"}.issubset(summary)


def test_signal_candidate_prefilter_keeps_all_live_signals() -> None:
    params = sample_params()
    features = build_feature_frame(
        ada_frame(),
        fast_ema=params.fast_ema,
        slow_ema=params.slow_ema,
        breakout_window=params.breakout_window,
        atr_window=params.atr_window,
    ).reset_index(drop=True)
    candidates = set(signal_candidate_indices(features))
    live_signals = {
        idx
        for idx, (_, row) in enumerate(features.iterrows())
        if build_signal(row, inst_id="ADA-USDT-SWAP", params=params, frame=features, idx=idx).accepted
    }
    assert live_signals
    assert live_signals <= candidates


def test_signal_candidate_prefilter_includes_pullback_continuation() -> None:
    rows = []
    for idx in range(12):
        rows.append(
            {
                "ts": f"2026-01-01T00:{idx:02d}:00Z",
                "open": 106.0 + idx * 0.1,
                "high": 108.0 + idx * 0.2,
                "low": 107.0,
                "close": 107.0 + idx * 0.1,
                "atr": 2.0,
                "atr_pct": 0.018,
                "vol_ratio": 2.2,
                "market_regime": "high_vol_trend",
                "trend_bias": "long",
                "breakout_high": 120.0,
                "breakout_low": 90.0,
                "ema_fast": 109.0,
                "ema_slow": 104.0,
            }
        )
    rows[8]["close"] = 107.5
    rows[9].update({"open": 110.0, "high": 112.0, "low": 108.6, "close": 111.4})
    features = pd.DataFrame(rows)

    signal = build_signal(features.iloc[9], inst_id="BTC-USDT-SWAP", params=StrategyParams(), frame=features, idx=9)

    assert signal.accepted
    assert 9 in set(signal_candidate_indices(features))
