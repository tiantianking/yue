# OKX Contract Signal System

Lightweight OKX USDT perpetual research and signal observation system.

Current boundary:
- Signal-only local research, backtesting, observation, and Feishu notification.
- Release defaults to `SIGNAL_ONLY`; no automatic execution entry is provided.
- OKX is used only as the market-data and instrument reference.
- Risk parameters are research estimates, not execution instructions.

Shadow research channel:
- `config/shadow_ensemble.yaml` enables the frozen 4h Donchian + volatility-compression ensemble.
- It consumes only closed 15m candles and writes `outputs/shadow_ensemble_status.json` plus an isolated `outputs/shadow_ensemble.sqlite3`.
- It is research-only and does not use the formal lifecycle, notification outbox, approved manifest, account, or order modules.
- The Dashboard displays it in a separate `SHADOW A-` panel.

Release safety:
- The product workflow is market data -> signal research -> Feishu push.
- Release packages must include `.env.example` only, never `.env`.
- Release packages must not contain OKX private credentials.
- Configuration must keep order submission and automatic close paths disabled.

Run checks:

```powershell
D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m pytest
D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe scripts\system_check.py all --mode observation
```

Automated research gate:

```powershell
# Decide whether the local 21-symbol dataset has enough history and new closed bars.
D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe scripts\system_check.py data

# Run the complete pre-PnL and post-backtest gate. Failed strategies are archived automatically.
D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe scripts\system_check.py research `
  --candidate config\research_candidates\MY_CANDIDATE.json `
  --artifacts outputs\research_runs\MY_RUN

# Mark the current latest closed bars only after a completed research cycle passes every gate.
D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe scripts\system_check.py research `
  --candidate config\research_candidates\MY_CANDIDATE.json `
  --artifacts outputs\research_runs\MY_RUN `
  --mark-researched
```

The gate derives parameter freedom, scans Python AST for future leakage, compares the structured family signature against the registry and archived failures, regenerates three cost-stress scenarios, checks symbol/month/top-trade concentration, and refuses a new study until sufficient new data exists. It never promotes parameters automatically.

Linux deployment:
- Run `deployment/install_linux.sh` from a reviewed release package.
- Start with `DEPLOYMENT_MODE=observation` and `FEISHU_ENABLED=false`.
- Use the single lightweight checker: `scripts/system_check.py preflight` before startup and `scripts/system_check.py runtime` during operation.
- Switch to production only after a legitimate current-version approved manifest exists.
- See `docs/DEPLOYMENT_CHECKLIST_CN.md` for the complete procedure.
