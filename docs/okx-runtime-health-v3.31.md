# OKX Signal System Runtime Health v3.31

## Business Outcome
- Phase 3 correlation-aware signal tiering is now in place.
- A-tier immediate pushes still cap at two candidates per closed-candle cycle.
- Highly correlated ready candidates now share a dynamic group, so each group can occupy at most one A-tier slot.
- Other ready candidates remain visible as B-tier candidates instead of being dropped.

## Scope
- This release does not enable real orders.
- This release does not change fixed strategy parameters.
- This release does not raise `min_signal_score=6.0`.
- This release only uses closed 15m candle history for correlation grouping.

## 2026-06-16 - Task: Add Correlation-Aware Signal Tiers

### What was done
- Added rolling return correlation grouping for signal candidates.
- Updated selector tiering so each correlation group can have at most one A-tier candidate while preserving the total A-tier cap.
- Passed the current closed K-line history from realtime and GUI scans into tier selection.
- Added correlation group details to health/dashboard status items.
- Updated version metadata to v3.31.

### Testing
- `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m compileall gui.py main.py src`
  passed.
- `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m pytest tests/test_signal_quality.py`
  passed, 4 tests.
- `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m pytest`
  passed, 106 tests.
- `npm.cmd run lint` from `D:\JIAOYI-CX\1_CODE_代码\okx-contract-signal-system\dashboard`
  passed.
- `npm.cmd run build` from `D:\JIAOYI-CX\1_CODE_代码\okx-contract-signal-system\dashboard`
  passed.

### Notes
- Changed files include signal quality correlation and selector modules, realtime monitor, GUI scanner, signal quality tests, version metadata, next-step handoff, progress log, and this runtime note.
- Rollback point: revert commit `feat: add correlation-aware signal tiers`.
