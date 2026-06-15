# OKX Signal System Runtime Health v3.33

## Business Outcome
- Phase 5 signal lifecycle tracking is now in place.
- Ready candidates are recorded as `TRIGGERED` with an invalidation price when they become push-eligible.
- Later closed candles update lifecycle records to `CONFIRMED`, `INVALIDATED`, or `EXPIRED`.
- Lifecycle data is persisted under `outputs/signal_lifecycle.json` and summarized in `latest_scan_status.json`.

## Scope
- This release does not enable real orders.
- This release does not change fixed strategy parameters.
- This release does not raise `min_signal_score=6.0`.
- Lifecycle confirmation and invalidation use closed K-lines only.

## 2026-06-16 - Task: Track Signal Lifecycle States

### What was done
- Added a signal lifecycle store for triggered, confirmed, invalidated, and expired signal states.
- Added invalidation price and lifecycle status to ready candidate payloads and health output.
- Connected realtime and GUI scans to update lifecycle records from closed candle history.
- Added Feishu lifecycle fields for A-tier alerts and B-tier summaries.
- Updated version metadata to v3.33.

### Testing
- `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m compileall gui.py main.py src`
  passed.
- `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m pytest`
  passed, 115 tests.
- `npm.cmd run lint` from `D:\JIAOYI-CX\1_CODE_代码\okx-contract-signal-system\dashboard`
  passed.
- `npm.cmd run build` from `D:\JIAOYI-CX\1_CODE_代码\okx-contract-signal-system\dashboard`
  passed.

### Notes
- Changed files include the lifecycle module, candidate metadata, realtime monitor, GUI scanner, Feishu notification helper, lifecycle tests, version metadata, next-step handoff, progress log, and this runtime note.
- Rollback point: revert commit `feat: track signal lifecycle states`.
