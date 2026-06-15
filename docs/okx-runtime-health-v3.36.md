# OKX Signal System Runtime Health v3.36

## Business Outcome
- Phase 9 model shadow scoring is now connected to realtime and GUI scans.
- Optional model artifacts are loaded from `outputs/signal_quality_model.json`.
- Candidate health output, ready-signal payloads, and dashboard status can show model probabilities and expected net R.
- Existing A/B-tier selection and push decisions remain unchanged.

## Scope
- This release does not enable real orders.
- This release does not change fixed strategy parameters.
- This release does not raise `min_signal_score=6.0`.
- This release does not use model output as a hard reject, promotion gate, or ranking adjustment.
- This release does not require a model artifact; missing artifacts degrade to disabled shadow status.

## 2026-06-16 - Task: Score Candidates With Quality Model Shadow Mode

### What was done
- Added optional quality model artifact loading and serialization helpers.
- Added a shadow scorer that builds signal-time features and emits model scores without changing decisions.
- Connected shadow score output to realtime and GUI scan health payloads.
- Added dashboard status fields for model TP probability and expected R.
- Updated version metadata to v3.36.

### Testing
- `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m compileall gui.py main.py src`
  passed.
- `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m pytest`
  passed, 130 tests.
- `npm.cmd run lint` from `D:\JIAOYI-CX\1_CODE_代码\okx-contract-signal-system\dashboard`
  passed.
- `npm.cmd run build` from `D:\JIAOYI-CX\1_CODE_代码\okx-contract-signal-system\dashboard`
  passed.

### Notes
- Changed files include the model serialization helper, shadow scorer, realtime monitor, GUI scanner, dashboard status types/view, shadow tests, version metadata, next-step handoff, progress log, and this runtime note.
- Rollback point: revert commit `feat: score candidates with quality model shadow mode`.
