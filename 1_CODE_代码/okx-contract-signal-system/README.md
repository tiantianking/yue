# OKX Contract Signal System

Lightweight OKX USDT perpetual research and signal observation system.

Current boundary:
- Signal-only local research, backtesting, observation, and Feishu notification.
- Release defaults to `SIGNAL_ONLY`; no automatic execution entry is provided.
- OKX is used only as the market-data and instrument reference.
- Risk parameters are research estimates, not execution instructions.

Release safety:
- The product workflow is market data -> signal research -> Feishu push.
- Release packages must include `.env.example` only, never `.env`.
- Release packages must not contain OKX private credentials.
- Configuration must keep order submission and automatic close paths disabled.

Run checks:

```powershell
D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m pytest
```
