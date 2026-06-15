# OKX Signal System Runtime Health v3.28

## Business Outcome
- Phase 1 signal push correctness is tightened without changing the main strategy parameters or raising the 6.0 score threshold.
- Historical accepted signals are no longer recovered with `accepted[-1]`; legacy scan paths now evaluate only the latest closed K-line.
- Signal notification de-duplication now uses a SQLite `pushed_signals` table by default and keys include symbol, candle time, side, strategy version, and parameter hash.
- Realtime monitor scanning no longer relies on a 10-second repeated loop plus 5-minute in-memory cooldown; it sleeps until the next closed-candle scan window.
- Signals older than the allowed closed-candle delay are blocked as `stale_signal_bar`.

## Scope
- This release does not train or enable a new quality model.
- This release does not enable real orders.
- This release does not modify the fixed strategy parameters:
  `fast_ema=120`, `slow_ema=720`, `breakout_window=384`, `atr_stop_mult=4.0`, `take_profit_mult=6.0`, `max_hold_bars=768`.
- This release does not raise `min_signal_score=6.0`.

## Verification
- Backend tests: `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m pytest`.
- Dashboard lint: `npm.cmd run lint` from the dashboard directory.
- Dashboard build: `npm.cmd run build` from the dashboard directory.

## 2026-06-16 - Task: Phase 1 Signal Push Correctness

### What was done
- Added shared signal runtime helpers for latest-closed signal evaluation, closed-candle lag checks, strategy version, parameter hash, and signal IDs.
- Upgraded signal push de-duplication to SQLite while preserving JSON compatibility for existing tests/tools.
- Updated realtime, GUI, scheduler, and TradingBrain paths to block stale signal bars and avoid historical signal recovery.
- Removed fixed quantity/leverage from TradingBrain signal push and used `RiskDecision` output instead.
- Updated version metadata to v3.28.

### Testing
- `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m compileall gui.py main.py src`
- `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m pytest tests/test_signal_runtime.py tests/test_feishu_notify.py tests/test_desktop_runtime.py`
- `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m pytest`
- `npm.cmd run lint` from `D:\JIAOYI-CX\1_CODE_代码\okx-contract-signal-system\dashboard`
- `npm.cmd run build` from `D:\JIAOYI-CX\1_CODE_代码\okx-contract-signal-system\dashboard`

### Notes
- Changed files include runtime signal helpers, notification de-duplication, realtime monitor, GUI monitor, TradingBrain, scheduler, Feishu health labels, tests, and version metadata.
- Rollback point: revert commit `fix: harden signal push correctness`.
