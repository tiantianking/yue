# OKX Signal System Runtime Health v3.32

## Business Outcome
- Phase 4 B-tier summary push is now in place.
- B-tier candidates are summarized at most once per closed-candle cycle instead of only staying in logs/status.
- A-tier individual signal push behavior remains unchanged.
- B-tier summary de-duplication uses a separate SQLite store from A-tier signal push de-duplication.

## Scope
- This release does not enable real orders.
- This release does not change fixed strategy parameters.
- This release does not raise `min_signal_score=6.0`.
- This release does not use B-tier summaries as a hard reject or promotion gate.

## 2026-06-16 - Task: Summarize B-Tier Signal Candidates

### What was done
- Added a Feishu B-tier summary helper.
- Added independent B-tier summary notification keys and a separate SQLite summary store.
- Updated realtime monitor and GUI scans to send at most one B-tier summary per closed-candle cycle.
- Kept A-tier individual push and de-duplication behavior unchanged.
- Updated version metadata to v3.32.

### Testing
- `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m compileall gui.py main.py src`
  passed.
- `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m pytest tests/test_feishu_notify.py`
  passed, 10 tests.
- `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m pytest`
  passed, 109 tests.
- `npm.cmd run lint` from `D:\JIAOYI-CX\1_CODE_代码\okx-contract-signal-system\dashboard`
  passed.
- `npm.cmd run build` from `D:\JIAOYI-CX\1_CODE_代码\okx-contract-signal-system\dashboard`
  passed.

### Notes
- Changed files include Feishu notification helpers, notification de-duplication, realtime monitor, GUI scanner, Feishu tests, version metadata, next-step handoff, progress log, and this runtime note.
- Rollback point: revert commit `feat: summarize b tier signal candidates`.
