# OKX Signal System Runtime Health v3.29

## Business Outcome
- Phase 2 signal routing is now in place: the system collects all ready candidates in a scan cycle before deciding what to push.
- A-level signals are ranked across the watched symbols and capped at two immediate pushes per closed-candle cycle.
- Remaining ready candidates are retained as B-level candidates for the health summary and dashboard status instead of being dropped.
- The main strategy threshold remains unchanged; this release does not raise `min_signal_score=6.0`.

## Scope
- This release does not train or enable a quality model.
- This release does not enable real orders.
- This release does not add correlation grouping yet; high-correlation de-duplication remains a next phase.
- This release keeps fixed strategy parameters unchanged.

## 2026-06-16 - Task: Batch Rank And Tier Signal Pushes

### What was done
- Added `signal_quality` candidate, ranker, and selector modules.
- Updated realtime monitor and GUI scanning to collect all ready candidates, assign A/B tiers, and only immediately push A-level candidates.
- Added tier/rank fields to pushed signal text.
- Added tests for tier selection and push message ranking fields.
- Updated version metadata to v3.29.

### Testing
- `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m compileall gui.py main.py src`
- `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m pytest tests/test_signal_quality.py tests/test_feishu_notify.py tests/test_desktop_runtime.py`
- `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m pytest`
- `npm.cmd run lint` from `D:\JIAOYI-CX\1_CODE_代码\okx-contract-signal-system\dashboard`
- `npm.cmd run build` from `D:\JIAOYI-CX\1_CODE_代码\okx-contract-signal-system\dashboard`

### Notes
- Changed files include realtime monitor, GUI scanner, Feishu signal template, signal quality modules, tests, and version metadata.
- Rollback point: revert commit `feat: batch rank and tier signal pushes`.
