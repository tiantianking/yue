"""Quick check latest signals"""
import sys
sys.path.insert(0, r'D:\JIAOYI-CX\1_CODE_代码\okx-contract-signal-system\src')
from okx_signal_system.data.loader import list_parquet_files, load_symbol_file
from okx_signal_system.features.indicators import build_feature_frame
from okx_signal_system.strategy.trend_breakout import StrategyParams, generate_signals

files = list_parquet_files('okx_1h_extended')
params = StrategyParams()
all_signals = []

for f in files:
    try:
        sd = load_symbol_file(f)
        features = build_feature_frame(sd.frame)
        signals = generate_signals(features, inst_id=sd.inst_id, params=params)
        accepted = [s for s in signals if s.accepted]
        if accepted:
            latest = accepted[-1]
            bar_idx = len(features) - 1
            all_signals.append({
                'symbol': sd.inst_id.replace('-USDT-SWAP', ''),
                'side': latest.side,
                'entry': latest.entry_ref or features.iloc[bar_idx]['close'],
                'sl': latest.stop_loss or 0,
                'tp': latest.take_profit or 0,
                'ts': features.iloc[bar_idx]['ts'],
                'close': features.iloc[bar_idx]['close'],
            })
    except Exception as e:
        print('Error:', e)

all_signals.sort(key=lambda x: x['ts'], reverse=True)
print('Symbol   Direction  Entry       Close      SL        TP        Time')
print('-' * 90)
for s in all_signals:
    ts = s['ts'].strftime('%Y-%m-%d %H:%M')
    direction = '[LONG]' if s['side'] == 'long' else '[SHORT]'
    print(f"{s['symbol']:<8} {direction} {s['entry']:>10.2f} {s['close']:>10.2f} {s['sl']:>10.2f} {s['tp']:>10.2f}  {ts}")

print()
print('Total symbols with signals:', len(all_signals))

# Signal strength analysis
print()
print('=== Signal Strength Analysis ===')
for s in all_signals[:10]:
    if s['entry'] and s['close']:
        diff_pct = abs(s['close'] - s['entry']) / s['entry'] * 100
        risk_reward = 0
        if s['entry'] > 0 and s['sl'] > 0 and s['tp'] > 0:
            sl_dist = abs(s['entry'] - s['sl']) / s['entry'] * 100
            tp_dist = abs(s['tp'] - s['entry']) / s['entry'] * 100
            if sl_dist > 0:
                risk_reward = tp_dist / sl_dist
        print(f"{s['symbol']}: diff={diff_pct:.2f}%, RR={risk_reward:.2f}")