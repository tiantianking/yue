# OKX Signal System Runtime Health v3.34

## Business Outcome
- Phase 6 historical candidate labeling is now in place.
- Historical signals can be labeled as `TP`, `SL`, or `TIMEOUT` without using candles at or before the signal time.
- Same-candle TP/SL conflicts are handled conservatively as `SL`.
- Label output includes net R after existing fee, slippage, and funding cost rules, plus MAE, MFE, holding bars, exit time, and exit price.

## Scope
- This release does not enable real orders.
- This release does not change fixed strategy parameters.
- This release does not raise `min_signal_score=6.0`.
- This release does not use labels as a live reject or promotion gate.

## 2026-06-16 - Task: Label Historical Signal Outcomes

### What was done
- Added a signal labeler for historical candidate outcomes.
- Applied stop-loss-first handling when TP and SL occur on the same candle.
- Filtered labels to later closed candles only.
- Added focused tests for TP, SL, TIMEOUT, same-candle conflict, and closed-candle boundaries.
- Updated version metadata to v3.34.

### Testing
- `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m compileall gui.py main.py src`
  passed.
- `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m pytest`
  passed, 120 tests.
- `npm.cmd run lint` from `D:\JIAOYI-CX\1_CODE_代码\okx-contract-signal-system\dashboard`
  passed.
- `npm.cmd run build` from `D:\JIAOYI-CX\1_CODE_代码\okx-contract-signal-system\dashboard`
  passed.

### Notes
- Changed files include the labeler module, signal quality exports, labeler tests, version metadata, next-step handoff, progress log, and this runtime note.
- Rollback point: revert commit `feat: label historical signal outcomes`.
