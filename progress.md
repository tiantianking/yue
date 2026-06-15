## 2026-06-16 - Task: Restore true healthy runtime for OKX signal system v3.26
### What was done
- Changed realtime startup from fallback-style success to verified WebSocket success: the monitor now requires the OKX business WebSocket to actually open before reporting healthy.
- Restored live K-line subscriptions through the OKX business endpoint, added local proxy support, and exposed connection details in the runtime status payload.
- Fixed realtime candle timestamp parsing and same-bar cache replacement so live data updates do not fail during normal WebSocket pushes.
- Added a runtime health panel/status payload so the desktop/dashboard can show whether WebSocket and scan refresh are genuinely healthy.
- Updated the health-report reason label for idle scan cycles and bumped the application version to v3.26 / 3.26.0.
### Testing
- `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m pytest`; result: 93 passed.
- `npm.cmd run lint` in `okx-contract-signal-system/dashboard`; result: passed.
- `npm.cmd run build` in `okx-contract-signal-system/dashboard`; result: passed.
- Checked `outputs/latest_scan_status.json`; result: WebSocket connected=true, degraded=false, 21 symbols subscribed, scan status running.
### Notes
- `okx-contract-signal-system/src/okx_signal_system/exchange/realtime.py` - Restored OKX business WebSocket usage, true connected-state reporting, proxy support, timestamp parsing, and same-bar cache updates.
- `okx-contract-signal-system/gui.py` - Writes fresh runtime scan status, stops monitoring when WebSocket is not actually connected, and shows v3.26.
- `okx-contract-signal-system/dashboard/src/components/dashboard.tsx` - Added the runtime health panel for WebSocket and scan freshness.
- `okx-contract-signal-system/dashboard/src/lib/types.ts` - Added runtime scan/WebSocket status fields used by the dashboard.
- `okx-contract-signal-system/src/okx_signal_system/notify/feishu.py` - Made waiting-for-next-bar health text readable.
- `okx-contract-signal-system/tests/test_data_layer.py` - Covered same-bar realtime cache updates.
- `okx-contract-signal-system/tests/test_desktop_runtime.py` - Covered business WebSocket endpoint, millisecond timestamp parsing, proxy options, and failed-connect reporting.
- `okx-contract-signal-system/tests/test_feishu_notify.py` - Covered the readable waiting-next-bar health reason.
- `okx-contract-signal-system/pyproject.toml`, `src/okx_signal_system/__init__.py`, `main.py`, `start.bat` - Bumped displayed/package versions to 3.26.0 / v3.26.
- `docs/okx-runtime-health-v3.26.md` - Documented the v3.26 runtime-health behavior and verification points.
- `progress.md` - Added this progress record.
- Rollback: after commit, run `git revert <this_commit_hash>` from `D:\JIAOYI-CX`; before commit, use `git restore` on the listed tracked files and remove `docs/okx-runtime-health-v3.26.md` plus this progress entry.