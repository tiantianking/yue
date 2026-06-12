"""
系统验证测试：测试优化后的参数选择逻辑和信号生成
核心验证：盈亏比为主，胜率为辅
"""
import sys
sys.path.insert(0, 'src')

import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime, timezone

# 导入系统模块
from okx_signal_system.backtest.research import (
    run_shared_train_grid,
    select_shared_params,
    run_walk_forward_validation,
    run_dataset_research_artifacts,
)
from okx_signal_system.backtest.evaluation import EvaluationThresholds
from okx_signal_system.data.loader import load_all_symbols, SymbolData
from okx_signal_system.strategy.trend_breakout import StrategyParams
from okx_signal_system.features.indicators import build_feature_frame
from okx_signal_system.backtest.runner import run_backtest, split_train_valid, summarize_trades

# 数据路径
DATA_DIR = Path(r"D:\JIAOYI-CX\历史数据_保留\lightweight_history\okx_1h_extended")
DATASET = "okx_1h_extended"

# 策略参数
DEFAULT_PARAMS = StrategyParams(
    fast_ema=20,
    slow_ema=60,
    breakout_window=40,
    atr_stop_mult=2.0,
    take_profit_mult=2.0,
    max_hold_bars=48,
    atr_window=14,
)


def test_data_coverage():
    """Test 1: Data Coverage"""
    print("\n" + "="*60)
    print("Test 1: Data Coverage Check")
    print("="*60)

    files = sorted(DATA_DIR.glob("*.parquet"))
    print(f"Found {len(files)} data files\n")

    for f in files:
        df = pd.read_parquet(f)
        ts = pd.to_datetime(df['ts'], utc=True)
        count = len(df)
        start = ts.min()
        end = ts.max()
        days = (end - start).days
        bar_type = "[OK]" if days > 1400 else "[!]"
        print(f"{bar_type} {f.stem:30s} | {start.strftime('%Y-%m')} ~ {end.strftime('%Y-%m')} | {days:4d} days | {count:6d} bars")


def test_parameter_selection():
    """Test 2: Parameter Selection Logic (PF first)"""
    print("\n" + "="*60)
    print("Test 2: Parameter Selection Logic")
    print("="*60)

    # Load BTC data
    btc_file = DATA_DIR / "BTC_USDT_USDT_1h.parquet"
    if not btc_file.exists():
        print("[X] BTC data file not found")
        return

    df = pd.read_parquet(btc_file)
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    print(f"BTC data: {len(df)} bars")

    # Split train/valid
    train_frame, valid_frame = split_train_valid(df, valid_fraction=0.25)
    print(f"Train: {len(train_frame)} bars, Valid: {len(valid_frame)} bars")

    # Run grid search
    print("\nRunning grid search (1296 combinations)...")
    from okx_signal_system.backtest.grid_search import parameter_grid, run_grid_search, select_best_params

    grid = run_grid_search(train_frame, inst_id="BTC-USDT-SWAP")
    print(f"Grid search completed: {len(grid)} rows")

    # Show Top10 params (sorted by PF)
    grid["rank_pf"] = grid["train_profit_factor"].replace(float("inf"), 999999)
    top10 = grid.nlargest(10, "rank_pf")

    print("\nTop10 Params (sorted by PF):")
    print("-" * 80)
    print(f"{'PF':>8} | {'WinRate':>8} | {'Payoff':>8} | {'Return':>10} | {'Trades':>8} | Drawdown")
    print("-" * 80)

    for _, row in top10.iterrows():
        pf = row["rank_pf"] if row["rank_pf"] != 999999 else "inf"
        pf_str = f"{pf:.2f}" if isinstance(pf, float) else pf
        print(f"{pf_str:>8} | {row['train_win_rate']:>7.1%} | {row['train_payoff_ratio']:>8.2f} | "
              f"{row['train_total_return']:>10.2%} | {row['train_total_trades']:>8} | {row['train_max_drawdown']:>6.2%}")

    # Verify selected params
    selected = select_best_params(grid)
    print(f"\n[V] Selected params: {selected}")
    print(f"   - fast_ema={selected.fast_ema}, slow_ema={selected.slow_ema}")
    print(f"   - breakout_window={selected.breakout_window}")
    print(f"   - atr_stop_mult={selected.atr_stop_mult}, take_profit_mult={selected.take_profit_mult}")


def test_signal_generation():
    """Test 3: Signal Generation Logic"""
    print("\n" + "="*60)
    print("Test 3: Signal Generation Logic")
    print("="*60)

    btc_file = DATA_DIR / "BTC_USDT_USDT_1h.parquet"
    if not btc_file.exists():
        print("[X] BTC data file not found")
        return

    df = pd.read_parquet(btc_file)
    df["ts"] = pd.to_datetime(df["ts"], utc=True)

    # Build features (only pass parameters that build_feature_frame accepts)
    feature_params = {
        'fast_ema': DEFAULT_PARAMS.fast_ema,
        'slow_ema': DEFAULT_PARAMS.slow_ema,
        'breakout_window': DEFAULT_PARAMS.breakout_window,
        'atr_window': DEFAULT_PARAMS.atr_window,
    }
    features = build_feature_frame(df, **feature_params)
    print(f"Features built: {len(features)} bars")

    # Generate signals
    from okx_signal_system.strategy.trend_breakout import generate_signals
    signals = generate_signals(features, inst_id="BTC-USDT-SWAP", params=DEFAULT_PARAMS)

    # Stats
    total = len(signals)
    accepted = sum(1 for s in signals if s.accepted)
    rejected = total - accepted

    # Rejection reasons
    reject_reasons = {}
    for s in signals:
        if s.reject_reason:
            reject_reasons[s.reject_reason] = reject_reasons.get(s.reject_reason, 0) + 1

    print(f"\nSignal Stats:")
    print(f"  Total signals: {total}")
    print(f"  Accepted: {accepted} ({accepted/total*100:.1f}%)")
    print(f"  Rejected: {rejected} ({rejected/total*100:.1f}%)")

    print(f"\nRejection reasons:")
    for reason, count in sorted(reject_reasons.items(), key=lambda x: -x[1]):
        print(f"  {reason:30s}: {count:6d} ({count/rejected*100:.1f}%)")


def test_walk_forward():
    """Test 4: Walk-Forward Stability Validation"""
    print("\n" + "="*60)
    print("Test 4: Walk-Forward Stability Validation")
    print("="*60)

    btc_file = DATA_DIR / "BTC_USDT_USDT_1h.parquet"
    if not btc_file.exists():
        print("[X] BTC data file not found")
        return

    df = pd.read_parquet(btc_file)
    df["ts"] = pd.to_datetime(df["ts"], utc=True)

    # Walk-Forward test
    wf_result = run_walk_forward_validation(
        df,
        inst_id="BTC-USDT-SWAP",
        params=DEFAULT_PARAMS,
        train_window=1000,
        valid_window=250,
        step=250,
    )

    if not wf_result:
        print("[X] Walk-Forward validation data insufficient")
        return

    print(f"\nWalk-Forward Results ({wf_result['window_count']} windows):")
    print(f"  Valid PF Mean: {wf_result['valid_pf_mean']:.3f}")
    print(f"  Valid PF Std: {wf_result['valid_pf_std']:.3f}")
    print(f"  Valid PF CV: {wf_result['valid_pf_cv']:.3f} (lower = more stable)")
    print(f"  Valid Return Mean: {wf_result['valid_return_mean']:.2%}")
    print(f"  Valid Win Rate Mean: {wf_result['valid_win_rate_mean']:.1%}")

    # Stability rating
    cv = wf_result['valid_pf_cv']
    if cv < 0.3:
        print(f"\n[V] Stability Rating: EXCELLENT (CV={cv:.3f})")
    elif cv < 0.5:
        print(f"\n[V] Stability Rating: GOOD (CV={cv:.3f})")
    elif cv < 1.0:
        print(f"\n[!] Stability Rating: FAIR (CV={cv:.3f})")
    else:
        print(f"\n[X] Stability Rating: POOR (CV={cv:.3f})")


def test_evaluation_thresholds():
    """Test 5: Evaluation Thresholds Configuration"""
    print("\n" + "="*60)
    print("Test 5: Evaluation Thresholds")
    print("="*60)

    thresholds = EvaluationThresholds()

    print("\nCurrent Thresholds:")
    print(f"  Min Profit Factor (PF): {thresholds.min_valid_profit_factor:.2f}")
    print(f"  Min Payoff Ratio: {thresholds.min_valid_payoff_ratio:.2f}")
    print(f"  Max Drawdown: {thresholds.max_valid_drawdown:.0%}")
    print(f"  Min Win Rate: {thresholds.min_win_rate:.0%}")
    print(f"  High PF Win Rate Threshold: {thresholds.high_pf_win_rate_threshold:.2f} (win rate can be >=30%)")

    print("\nCore Principles:")
    print("  1. Profit Factor (PF) >= 1.05 is the baseline")
    print("  2. When PF >= 1.5, win rate can be lowered to 30%")
    print("  3. Win rate is only auxiliary, high PF + reasonable win rate is acceptable")
    print("  4. High PF + Low Win Rate = Trend Following (big wins, small losses)")


def test_full_backtest():
    """Test 6: Full Backtest Validation"""
    print("\n" + "="*60)
    print("Test 6: Full Backtest Validation")
    print("="*60)

    btc_file = DATA_DIR / "BTC_USDT_USDT_1h.parquet"
    if not btc_file.exists():
        print("[X] BTC data file not found")
        return

    df = pd.read_parquet(btc_file)
    df["ts"] = pd.to_datetime(df["ts"], utc=True)

    # Split train/valid
    train_frame, valid_frame = split_train_valid(df, valid_fraction=0.25)

    # Run backtest
    train_trades = run_backtest(train_frame, inst_id="BTC-USDT-SWAP", params=DEFAULT_PARAMS)
    valid_trades = run_backtest(valid_frame, inst_id="BTC-USDT-SWAP", params=DEFAULT_PARAMS)

    train_summary = summarize_trades(train_trades)
    valid_summary = summarize_trades(valid_trades)

    print("\nTrain Set Results:")
    print(f"  Trades: {train_summary['total_trades']}")
    print(f"  Return: {train_summary['total_return']:.2%}")
    print(f"  PF: {train_summary['profit_factor']:.3f}")
    print(f"  Win Rate: {train_summary['win_rate']:.1%}")
    print(f"  Payoff: {train_summary['payoff_ratio']:.2f}")
    print(f"  Max Drawdown: {train_summary['max_drawdown']:.2%}")

    print("\nValid Set Results:")
    print(f"  Trades: {valid_summary['total_trades']}")
    print(f"  Return: {valid_summary['total_return']:.2%}")
    print(f"  PF: {valid_summary['profit_factor']:.3f}")
    print(f"  Win Rate: {valid_summary['win_rate']:.1%}")
    print(f"  Payoff: {valid_summary['payoff_ratio']:.2f}")
    print(f"  Max Drawdown: {valid_summary['max_drawdown']:.2%}")

    # Evaluate
    from okx_signal_system.backtest.evaluation import evaluate_symbol
    eval_result = evaluate_symbol(train_summary, valid_summary)

    print(f"\nEvaluation: {eval_result['pass_fail'].upper()}")
    if eval_result['reasons']:
        print(f"Fail reasons: {eval_result['reasons']}")


def main():
    """Main test function"""
    print("\n" + "="*60)
    print("OKX Contract Signal System - Optimization Verification")
    print("Core Principle: Profit Factor is primary, Win Rate is auxiliary")
    print("="*60)

    # Execute all tests
    test_data_coverage()
    test_evaluation_thresholds()
    test_signal_generation()
    test_parameter_selection()
    test_walk_forward()
    test_full_backtest()

    print("\n" + "="*60)
    print("[OK] All tests completed!")
    print("="*60)


if __name__ == "__main__":
    main()