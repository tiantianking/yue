"""Quick test 1: Data coverage and signal generation"""
import sys
sys.path.insert(0, 'src')

import pandas as pd
from pathlib import Path
from okx_signal_system.features.indicators import build_feature_frame
from okx_signal_system.strategy.trend_breakout import generate_signals, StrategyParams

DATA_DIR = Path(r"D:\JIAOYI-CX\历史数据_保留\lightweight_history\okx_1h_extended")

print("="*60)
print("Quick Test 1: Data Coverage + Signal Generation")
print("="*60)

# Load BTC data
btc_file = DATA_DIR / "BTC_USDT_USDT_1h.parquet"
df = pd.read_parquet(btc_file)
df["ts"] = pd.to_datetime(df["ts"], utc=True)
print(f"\nBTC data: {len(df)} bars from {df['ts'].min()} to {df['ts'].max()}")

# Build features with default params
params = StrategyParams(fast_ema=20, slow_ema=60, breakout_window=40, atr_window=14)
features = build_feature_frame(df, fast_ema=20, slow_ema=60, breakout_window=40, atr_window=14)
print(f"Features built: {len(features)} bars")

# Generate signals
signals = generate_signals(features, inst_id="BTC-USDT-SWAP", params=params)
total = len(signals)
accepted = sum(1 for s in signals if s.accepted)
rejected = total - accepted

print(f"\nSignal Stats:")
print(f"  Total signals: {total}")
print(f"  Accepted: {accepted} ({accepted/total*100:.1f}%)")
print(f"  Rejected: {rejected} ({rejected/total*100:.1f}%)")

# Rejection reasons
reject_reasons = {}
for s in signals:
    if s.reject_reason:
        reject_reasons[s.reject_reason] = reject_reasons.get(s.reject_reason, 0) + 1

print(f"\nRejection reasons (top 5):")
for reason, count in sorted(reject_reasons.items(), key=lambda x: -x[1])[:5]:
    print(f"  {reason:30s}: {count:6d} ({count/rejected*100:.1f}%)")

print("\n[OK] Test 1 completed!")