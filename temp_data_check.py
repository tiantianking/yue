import pandas as pd
from pathlib import Path

data_dir = Path(r'D:\JIAOYI-CX\历史数据_保留\lightweight_history\okx_1h_extended')
files = sorted(data_dir.glob('*.parquet'))

print('='*60)
print('各币种数据时间范围和条数')
print('='*60)

for f in files:
    df = pd.read_parquet(f)
    ts = pd.to_datetime(df['ts'], utc=True)
    count = len(df)
    start = ts.min()
    end = ts.max()
    days = (end - start).days
    print(f'{f.stem:30s} | {start.strftime("%Y-%m")} ~ {end.strftime("%Y-%m")} | {days:4d}天 | {count:6d}条')