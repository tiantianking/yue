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
```
