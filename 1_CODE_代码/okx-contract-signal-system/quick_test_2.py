"""Quick test 2: Backtest and PF-focused parameter selection"""
import sys
sys.path.insert(0, 'src')

import pandas as pd
from pathlib import Path
from okx_signal_system.backtest.runner import run_backtest, split_train_valid, summarize_trades
from okx_signal_system.backtest.grid_search import run_grid_search, select_best_params
from okx_signal_system.backtest.evaluation import evaluate_symbol, EvaluationThresholds
from okx_signal_system.strategy.trend_breakout import StrategyParams

DATA_DIR = Path(r"D:\JIAOYI-CX\历史数据_保留\lightweight_history\okx_1h_extended")

print("="*60)
print("Quick Test 2: Backtest + PF-First Parameter Selection")
print("="*60)

# Load BTC data
btc_file = DATA_DIR / "BTC_USDT_USDT_1h.parquet"
df = pd.read_parquet(btc_file)
df["ts"] = pd.to_datetime(df["ts"], utc=True)
print(f"\nBTC data: {len(df)} bars")

# Split train/valid
train_frame, valid_frame = split_train_valid(df, valid_fraction=0.25)
print(f"Train: {len(train_frame)} bars, Valid: {len(valid_frame)} bars")

# Quick grid search with limited params for speed
print("\nRunning grid search (sample)...")
params_grid = [
    StrategyParams(fast_ema=20, slow_ema=60, breakout_window=40, atr_stop_mult=2.0, take_profit_mult=2.0, max_hold_bars=48),
    StrategyParams(fast_ema=20, slow_ema=60, breakout_window=40, atr_stop_mult=2.0, take_profit_mult=3.0, max_hold_bars=48),
    StrategyParams(fast_ema=20, slow_ema=60, breakout_window=40, atr_stop_mult=1.5, take_profit_mult=2.0, max_hold_bars=48),
    StrategyParams(fast_ema=20, slow_ema=80, breakout_window=40, atr_stop_mult=2.0, take_profit_mult=2.0, max_hold_bars=48),
    StrategyParams(fast_ema=30, slow_ema=60, breakout_window=40, atr_stop_mult=2.0, take_profit_mult=2.0, max_hold_bars=48),
]

grid = run_grid_search(train_frame, inst_id="BTC-USDT-SWAP", params_grid=params_grid)
print(f"Grid search completed: {len(grid)} parameter combinations")

# Show all results sorted by PF
grid["rank_pf"] = grid["train_profit_factor"].replace(float("inf"), 999999)
sorted_grid = grid.sort_values("rank_pf", ascending=False)

print("\nAll Parameters (sorted by PF):")
print("-" * 80)
print(f"{'PF':>8} | {'WinRate':>8} | {'Payoff':>8} | {'Return':>10} | {'Trades':>8}")
print("-" * 80)

for _, row in sorted_grid.iterrows():
    pf = row["rank_pf"] if row["rank_pf"] != 999999 else "inf"
    pf_str = f"{pf:.2f}" if isinstance(pf, float) else pf
    print(f"{pf_str:>8} | {row['train_win_rate']:>7.1%} | {row['train_payoff_ratio']:>8.2f} | "
          f"{row['train_total_return']:>10.2%} | {row['train_total_trades']:>8}")

# Select best params (PF-first)
selected = select_best_params(grid)
print(f"\n[V] Selected params (PF-first):")
print(f"   fast_ema={selected.fast_ema}, slow_ema={selected.slow_ema}")
print(f"   breakout_window={selected.breakout_window}")
print(f"   atr_stop_mult={selected.atr_stop_mult}, take_profit_mult={selected.take_profit_mult}")

# Run backtest with selected params
print("\nRunning backtest with selected params...")
train_trades = run_backtest(train_frame, inst_id="BTC-USDT-SWAP", params=selected)
valid_trades = run_backtest(valid_frame, inst_id="BTC-USDT-SWAP", params=selected)

train_summary = summarize_trades(train_trades)
valid_summary = summarize_trades(valid_trades)

print("\nTrain Set Results:")
print(f"  Trades: {train_summary['total_trades']}")
print(f"  Return: {train_summary['total_return']:.2%}")
print(f"  PF: {train_summary['profit_factor']:.3f}")
print(f"  Win Rate: {train_summary['win_rate']:.1%}")
print(f"  Payoff: {train_summary['payoff_ratio']:.2f}")

print("\nValid Set Results:")
print(f"  Trades: {valid_summary['total_trades']}")
print(f"  Return: {valid_summary['total_return']:.2%}")
print(f"  PF: {valid_summary['profit_factor']:.3f}")
print(f"  Win Rate: {valid_summary['win_rate']:.1%}")
print(f"  Payoff: {valid_summary['payoff_ratio']:.2f}")

# Evaluate
eval_result = evaluate_symbol(train_summary, valid_summary)
print(f"\nEvaluation: {eval_result['pass_fail'].upper()}")
if eval_result['reasons']:
    print(f"Fail reasons: {eval_result['reasons']}")

# Core principle verification
print("\n" + "="*60)
print("Core Principle Verification:")
print("="*60)
pf = valid_summary['profit_factor']
win_rate = valid_summary['win_rate']
print(f"  Valid PF: {pf:.3f} (threshold: 1.05)")
print(f"  Valid Win Rate: {win_rate:.1%} (threshold: 35%, or 30% if PF >= 1.5)")
if pf >= 1.5:
    print(f"  [OK] High PF ({pf:.2f}) >= 1.5, win rate threshold relaxed to 30%")
    if win_rate >= 0.30:
        print(f"  [OK] Win rate {win_rate:.1%} >= 30% (acceptable for high PF)")
else:
    if win_rate >= 0.35:
        print(f"  [OK] Win rate {win_rate:.1%} >= 35% (meets standard threshold)")

print("\n[OK] Test 2 completed!")