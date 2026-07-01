# OKX Contract Signal System

Lightweight OKX USDT perpetual research and signal observation system.

Project documentation:
- Chinese project overview: `docs/PROJECT_OVERVIEW_CN.md`.
- Change, failure-archive, and synchronization policy: `docs/CHANGE_CONTROL_POLICY_CN.md`.
- Failed research is copied to the user's Desktop `失败策略` archive and indexed by `scripts/refresh_failure_archive.py`.

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

The gate derives parameter freedom, scans Python AST for future leakage, compares the structured family signature against the registry and archived failures, verifies a hashed 6–10 month sealed holdout, requires a complete family trial ledger, point-in-time field evidence, a complete code-dependency manifest, and purge coverage of the full outcome horizon, then regenerates three cost-stress scenarios and checks symbol/month/top-trade concentration. Missing evidence, a reopened holdout, or unresolved leakage blocks the candidate. It never promotes parameters automatically.

Change governance:

```powershell
py -3.12 scripts\check_change_governance.py
py -3.12 scripts\refresh_failure_archive.py
CHECK_REMOTE_SYNC.cmd
```

Behavioral changes must update the project overview, current release note, package version, release manifest, tests, and local Git history. The final synchronization check only passes when the project worktree is clean and the local tracked branch matches its configured upstream.

Parallel forward acceptance:
- `RUN_PARALLEL_ACCEPTANCE.cmd` refreshes every registered research-shadow track, applies each track's frozen frequency-aware sample profile, and writes `outputs/parallel_acceptance_status.json`.
- Existing momentum shadows remain explicitly `RESEARCH_ONLY / NOT_A_TIER / SIGNAL_ONLY`; the staggered 3x3 low-turnover execution variant has its own daily forward ledger and cannot replace or retroactively rescue the fixed three-day track.
- The pre-existing 4h Donchian slow-trend and volatility-compression shadow ensemble is adapted from its isolated SQLite ledger into the same governance without using warmup records.
- New tracks require a passed `okx_research_gate_report_v2`; serious frozen-rule failures are archived permanently and cannot be rescued by retrospective tuning.
- The early-stop protocol is checksum-frozen in `config/parallel_acceptance_early_stop_protocol.json`; edited thresholds are rejected.
- `scripts/run_candidate_factory.py` batch-runs all registered schema-v2 candidates through the same frozen research gate without auto-promotion.
- See `docs/PARALLEL_FORWARD_ACCEPTANCE_CN.md`.

Linux deployment:
- Run `deployment/install_linux.sh` from a reviewed release package.
- Start with `DEPLOYMENT_MODE=observation` and `FEISHU_ENABLED=false`.
- Use the single lightweight checker: `scripts/system_check.py preflight` before startup and `scripts/system_check.py runtime` during operation.
- Switch to production only after a legitimate current-version approved manifest exists.
- See `docs/DEPLOYMENT_CHECKLIST_CN.md` for the complete procedure.
