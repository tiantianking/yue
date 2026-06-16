# OKX Signal System Runtime Health v3.39

## Business Outcome
- Fixed several v3.38 audit findings that affect long-running manual signal stability.
- Closed-candle filtering now treats string values such as `"False"` and `"0"` as open candles instead of accepting them.
- Signal scans now reject future-dated closed candles and keep retry rights when feature generation or feature validation fails.
- Realtime publishing now consumes the tier selection returned by `SignalScanService` instead of running A/B tiering a second time.
- External history lookup can now be configured with `data.root_dir`, `JIAOYI_DATA_DIR`, or explicit runtime roots, and the dashboard no longer relies on a hard-coded local history path.

## Scope
- This release does not enable real orders.
- This release does not modify OKX order submission, reduce-only handling, protection orders, or automatic close behavior.
- Trading-entry safety items from the v3.38 audit remain explicit approval items because they touch live-order and position-management boundaries.
- Existing fixed strategy parameters, `min_signal_score=6.0`, and quality-model shadow-only posture are unchanged.

## 2026-06-16 - Task: Fix Scan Consistency and Data Path Audit Items

### What was done
- Added strict closed-candle flag parsing for boolean, numeric, and string values.
- Rejected unexpected future closed bars in shared scan logic.
- Delayed `checked_bars` commits until scan feature construction and validation succeed.
- Removed duplicate realtime A/B tier assignment after shared scan service selection.
- Added explicit external history root support through function argument, `JIAOYI_DATA_DIR`, or `config/base.yaml` `data.root_dir`.
- Routed startup gap sync through the same external history resolver instead of its old local fallback path.
- Updated dashboard history path resolution to use `OKX_HISTORY_DIR`, `OKX_HISTORY_BASE`, `JIAOYI_DATA_DIR`, or `data.root_dir`.
- Updated PyInstaller packaging so local history data is bundled only when `JIAOYI_DATA_DIR` is explicitly set.
- Set the local runtime `data.root_dir` to `D:/JIAOYI-CX/历史数据_保留` so this machine can start without extra environment variables.
- Updated version metadata to v3.39.

### Testing
- `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m compileall gui.py main.py src`
  passed.
- Focused tests passed:
  `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m pytest tests/test_data_layer.py tests/test_signal_scan_service.py tests/test_desktop_runtime.py::test_publish_tiered_candidates_uses_scan_service_selection tests/test_signal_quality.py`
  passed, 26 tests.
- `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m pytest`
  passed, 145 tests.
- `npm.cmd run lint`
  passed from `D:\JIAOYI-CX\1_CODE_代码\okx-contract-signal-system\dashboard`.
- `npm.cmd run build`
  passed from `D:\JIAOYI-CX\1_CODE_代码\okx-contract-signal-system\dashboard`.

### Notes
- Rollback point: revert the commit `fix: tighten scan data gates`.
