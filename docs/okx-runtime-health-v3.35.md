# OKX Signal System Runtime Health v3.35

## Business Outcome
- Phase 7 leakage-safe quality features are now available for signal analysis.
- Phase 8 baseline quality ranking is now available for historical evaluation and candidate ordering research.
- Feature building only uses signal-time and earlier closed K-lines.
- The baseline model is ranking-only and is not connected as a live reject or promotion gate.

## Scope
- This release does not enable real orders.
- This release does not change fixed strategy parameters.
- This release does not raise `min_signal_score=6.0`.
- This release does not use unfinished K-lines for feature generation, labels, or validation.
- This release does not apply model output to live push decisions.

## 2026-06-16 - Task: Build Baseline Signal Quality Model

### What was done
- Added closed-candle signal quality feature construction for historical signals.
- Added prefix-invariant feature tests to guard against future leakage.
- Added a baseline signal quality ranking model using existing `numpy` and `pandas` dependencies.
- Added purged walk-forward validation helpers for ordered time-series evaluation.
- Updated version metadata to v3.35.

### Testing
- `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m compileall gui.py main.py src`
  passed.
- `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m pytest`
  passed, 128 tests.
- `npm.cmd run lint` from `D:\JIAOYI-CX\1_CODE_代码\okx-contract-signal-system\dashboard`
  passed.
- `npm.cmd run build` from `D:\JIAOYI-CX\1_CODE_代码\okx-contract-signal-system\dashboard`
  passed.

### Notes
- Changed files include the feature builder, baseline model, package exports, feature/model tests, version metadata, next-step handoff, progress log, and this runtime note.
- Rollback point: revert commit `feat: build baseline signal quality model`.
