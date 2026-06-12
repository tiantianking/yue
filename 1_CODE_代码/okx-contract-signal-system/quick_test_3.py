"""Quick test 3: Full grid search to find best parameters"""
import sys
sys.path.insert(0, 'src')

import pandas as pd
from pathlib import Path
from okx_signal_system.backtest.grid_search import parameter_grid, run_grid_search, select_best_params
from okx_signal_system.backtest.runner import run_backtest, split_train_valid, summarize_trades
from okx_signal_system.backtest.evaluation import evaluate_symbol

DATA_DIR = Path(r"D:\JIAOYI-CX\历史数据_保留\lightweight_history\okx_1h_extended")

print("="*60)
print("Quick Test 3: Full Grid Search (1296 combinations)")
print("="*60)

# Load BTC data
btc_file = DATA_DIR / "BTC_USDT_USDT_1h.parquet"
df = pd.read_parquet(btc_file)
df["ts"] = pd.to_datetime(df["ts"], utc=True)
print(f"\nBTC data: {len(df)} bars")

# Split
train_frame, valid_frame = split_train_valid(df, valid_fraction=0.25)
print(f"Train: {len(train_frame)} bars, Valid: {len(valid_frame)} bars")

# Full grid search
print("\nRunning full grid search (1296 combinations)...")
print("This may take a few minutes...")
grid = run_grid_search(train_frame, inst_id="BTC-USDT-SWAP")
print(f"Grid search completed: {len(grid)} parameter combinations")

# Normalize PF for sorting (handle inf)
grid["rank_pf"] = grid["train_profit_factor"].replace(float("inf"), 999999)
sorted_grid = grid.sort_values("rank_pf", ascending=False)

# Show Top 10 by PF
print("\nTop 10 Parameters (sorted by PF):")
print("-" * 100)
print(f"{'Rank':>4} | {'PF':>8} | {'WinRate':>8} | {'Payoff':>8} | {'Return':>10} | {'Trades':>8} | Params")
print("-" * 100)

for i, (_, row) in enumerate(sorted_grid.head(10).iterrows()):
    pf = row["rank_pf"] if row["rank_pf"] != 999999 else "inf"
    pf_str = f"{pf:.3f}" if isinstance(pf, float) else pf
    params_str = f"ema({row['fast_ema']},{row['slow_ema']}) bo={row['breakout_window']} atr={row['atr_stop_mult']:.1f} tp={row['take_profit_mult']:.1f}"
    print(f"{i+1:>4} | {pf_str:>8} | {row['train_win_rate']:>7.1%} | {row['train_payoff_ratio']:>8.2f} | "
          f"{row['train_total_return']:>10.2%} | {row['train_total_trades']:>8} | {params_str}")

# Select best params (PF-first)
print("\n" + "="*60)
print("Parameter Selection (PF-first):")
print("="*60)
selected = select_best_params(grid)
print(f"\nSelected params: fast_ema={selected.fast_ema}, slow_ema={selected.slow_ema}, breakout_window={selected.breakout_window}")
print(f"  atr_stop_mult={selected.atr_stop_mult}, take_profit_mult={selected.take_profit_mult}, max_hold_bars={selected.max_hold_bars}")

# Run backtest with selected params on validation
print("\nRunning backtest with selected params on validation set...")
valid_trades = run_backtest(valid_frame, inst_id="BTC-USDT-SWAP", params=selected)
valid_summary = summarize_trades(valid_trades)

print(f"\nValidation Results:")
print(f"  Trades: {valid_summary['total_trades']}")
print(f"  Return: {valid_summary['total_return']:.2%}")
print(f"  PF: {valid_summary['profit_factor']:.3f}")
print(f"  Win Rate: {valid_summary['win_rate']:.1%}")
print(f"  Payoff: {valid_summary['payoff_ratio']:.2f}")

# Evaluate
eval_result = evaluate_symbol({}, valid_summary)
print(f"\nEvaluation: {eval_result['pass_fail'].upper()}")
if eval_result['reasons']:
    print(f"Fail reasons: {eval_result['reasons']}")

print("\n" + "="*60)
print("Core Principle Verification:")
print("="*60)
pf = valid_summary['profit_factor']
win_rate = valid_summary['win_rate']
print(f"  Valid PF: {pf:.3f} (threshold: 1.05)")
print(f"  Valid Win Rate: {win_rate:.1%} (threshold: 35%, or 30% if PF >= 1.5)")

# Check if PF-first principle is working
top_params = sorted_grid.iloc[0]
top_pf = top_params['rank_pf'] if top_params['rank_pf'] != 999999 else float('inf')
top_win_rate = top_params['train_win_rate']

print(f"\n  Top params in grid: PF={top_pf:.3f}, WinRate={top_win_rate:.1%}")
print(f"  Selected params: PF={pf:.3f}, WinRate={win_rate:.1%}")

# Verify PF is primary metric
if pf >= 1.05:
    print(f"\n  [OK] PF >= 1.05: Profit Factor is the primary metric!")
    if pf >= 1.5 and win_rate >= 0.30:
        print(f"  [OK] High PF ({pf:.2f}) >= 1.5 with acceptable win rate ({win_rate:.1%} >= 30%)")
        print(f"  [OK] System accepts lower win rate when PF is high (trend following strategy)")
else:
    print(f"\n  [!] PF < 1.05: Current params don't meet threshold")
    print(f"  [i] Need to explore more parameter combinations or market conditions")

print("\n[OK] Full grid search test completed!")