# OKX Signal System Runtime Health v3.38

## Business Outcome
- GUI and CLI signal scans now use one shared `SignalScanService` decision core.
- Closed-bar freshness, stale-signal blocking, feature generation, strategy signal building, ensemble vote, risk validation, quality shadow scoring, lifecycle recording, and A/B tier ranking now return through the same scan result structure.
- GUI and CLI still keep their own outer side effects: status files, Feishu pushes, desktop display, persistence, and position checks remain outside the shared service.
- Existing fixed strategy parameters, `min_signal_score=6.0`, A/B-tier policy, and manual-confirmation posture are unchanged.

## Scope
- This release does not enable real orders.
- This release does not change API authentication or order placement paths.
- This release does not make the shadow quality model a hard reject gate.
- This release does not change database schema.
- Runtime output files under `outputs/` are local state and are not part of the code package.

## 2026-06-16 - Task: Unify GUI and CLI Signal Scan Core

### What was done
- Added `SignalScanService` and `SignalScanContext` as the shared scan decision boundary.
- Moved duplicate GUI and CLI candidate decision logic into the service.
- Kept notification sending, latest status writing, data persistence, and position management in their original callers.
- Preserved configured shadow-score minimum closed-signal support for both GUI and CLI scans.
- Updated version metadata to v3.38.

### Testing
- `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m compileall gui.py main.py src`
  passed.
- Focused scan/runtime tests passed:
  `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m pytest tests/test_signal_scan_service.py tests/test_desktop_runtime.py tests/test_signal_quality_shadow.py tests/test_signal_runtime.py tests/test_feishu_notify.py`
  passed, 30 tests.
- Full pytest and dashboard lint/build were run before packaging; see `progress.md` for the final command evidence.

### Notes
- Rollback point: revert the commit `feat: unify signal scan service`.
