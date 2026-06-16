# OKX Signal System Runtime Health v3.37

## Business Outcome
- The 15m to 1h trend resample now uses the correct closed-bar boundary.
- A trend bar labeled `01:00` excludes the newly started `01:00` 15m bar and only uses bars that were closed before that label.
- Startup anti-future checks now cover this trend resample boundary, so this class of hidden future leakage is caught before monitoring is considered healthy.
- Existing strategy parameters, A/B-tier decisions, model shadow mode, and live-order posture are unchanged.

## Scope
- This release does not enable real orders.
- This release does not change fixed strategy parameters.
- This release does not make the shadow quality model affect ranking or push decisions.
- This release does not merge GUI and CLI scan paths; that remains the next architecture step from the v3.36 audit.

## 2026-06-16 - Task: Fix Trend Resample Boundary

### What was done
- Changed trend resampling from right-closed to left-closed bins while keeping right-side labels.
- Added a regression test proving the first completed 1h bar excludes the right-edge 15m bar.
- Added a startup quality check named `trend_resample_excludes_right_edge`.
- Updated version metadata to v3.37.

### Testing
- `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m compileall gui.py main.py src`
  passed.
- `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m pytest`
  passed, 131 tests.
- `npm.cmd run lint` from `D:\JIAOYI-CX\1_CODE_代码\okx-contract-signal-system\dashboard`
  passed.
- `npm.cmd run build` from `D:\JIAOYI-CX\1_CODE_代码\okx-contract-signal-system\dashboard`
  passed.

### Notes
- Rollback point: revert the upcoming commit `fix: correct trend resample boundary`.
