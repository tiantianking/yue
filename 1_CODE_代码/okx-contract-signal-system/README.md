# OKX Contract Signal System

Lightweight OKX USDT perpetual signal observation and Feishu notification system.

## Production boundary

The deployed package contains only modules needed for configuration checks, 21-symbol closed-candle data, frozen strategy parameters, signal generation, runtime risk controls, signal-quality filtering, lifecycle management, Dashboard status and Feishu notification.

The deployed package excludes local candidate factories, historical backtests, parameter searches, future-leak scans, overfitting gates, trial ledgers, failure archives and pre-PnL templates.

## Runtime signal gates retained

Research/runtime separation does not weaken formal signal safety. Notification remains blocked when a required runtime condition fails, including:

- approved manifest or parameter-hash validation;
- closed-candle completeness and freshness;
- configured 21-symbol coverage;
- signal freshness and causal signal timing;
- runtime risk validation and signal-quality tier selection;
- correlation control, duplicate suppression and expiry handling;
- Feishu production configuration;
- signal-only and no-order safety settings.

The V357 shadow files remain because the desktop Dashboard uses that isolated forward-observation channel. Shadow output cannot replace the approved strategy or enter the formal notification lifecycle.

## Local research boundary

The full local checkout keeps research tools outside the production release allow-list. Local research continues to use `scripts/system_check.py` for data readiness, future-function scans, overfitting gates, family de-duplication, cost stress, contribution concentration, sealed holdouts and failure archiving.

Example local-only command:

```powershell
D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe scripts\system_check.py research `
  --candidate config\research_candidates\MY_CANDIDATE.json `
  --artifacts outputs\research_runs\MY_RUN
```

The deployed runtime only reads and validates a frozen approved manifest.

## Runtime checks

```powershell
D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe scripts\runtime_check.py preflight --mode observation
D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe scripts\runtime_check.py runtime --mode observation
```

Linux services call only `scripts/runtime_check.py`. They do not import `scripts/system_check.py` or candidate research modules.

## Deployment

Run `deployment/install_linux.sh` from a reviewed release package. Start with `DEPLOYMENT_MODE=observation` and `FEISHU_ENABLED=false`. Switch to production only after a legitimate approved manifest exists and runtime preflight passes.

See `docs/PROJECT_OVERVIEW_CN.md`, `docs/DEPLOYMENT_CHECKLIST_CN.md`, `docs/RUNTIME_VERIFICATION.md` and `docs/SYSTEM_ARCHITECTURE.md`.
