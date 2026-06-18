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
- `outputs/runtime/approved_strategy_manifest.json`
- `outputs/selected_params.json` as a legacy fallback only
- `outputs/latest_signal.json`
- `outputs/15m_backfill_3y_report.json`
- 15m parquet candles from `OKX_HISTORY_DIR`, `OKX_HISTORY_BASE`, `JIAOYI_DATA_DIR`, or `config/base.yaml` `data.root_dir`

Optional environment variables:

- `OKX_SIGNAL_ROOT`
- `OKX_HISTORY_DIR`
- `OKX_HISTORY_BASE`
- `JIAOYI_DATA_DIR`
- `OKX_DASHBOARD_PYTHON`

`OKX_DASHBOARD_PYTHON` overrides the Python command; otherwise the dashboard uses `PYTHON`, then `python`.
`OKX_HISTORY_DIR` and `OKX_HISTORY_BASE` are explicit dashboard history overrides. Without them, dashboard scripts let the Python backend resolve `JIAOYI_DATA_DIR` or `config/base.yaml` `data.root_dir` using the same rules as the signal runtime.

## Check

```powershell
npm.cmd run check
```
