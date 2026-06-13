# OKX Signal Desk

Local web dashboard for the OKX contract signal system.

## Run

```powershell
npm.cmd run dev:local
```

Open http://127.0.0.1:3001.

## Data

The dashboard reads the existing local system files:

- `outputs/startup_quality_gate.json`
- `outputs/selected_params.json`
- `outputs/latest_signal.json`
- `outputs/15m_backfill_3y_report.json`
- 15m parquet candles under `D:\JIAOYI-CX\历史数据_保留\lightweight_history\okx_15m_extended`

Optional environment variables:

- `OKX_SIGNAL_ROOT`
- `OKX_HISTORY_DIR`
- `OKX_DASHBOARD_PYTHON`

## Check

```powershell
npm.cmd run check
```
