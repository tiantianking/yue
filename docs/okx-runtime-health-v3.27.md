# OKX Signal System Runtime Health v3.27

## Business Outcome
- The system no longer reports realtime monitoring as healthy just because a WebSocket thread started.
- Startup health now requires the OKX WebSocket to actually open and stay connected.
- If realtime WebSocket startup fails, monitoring stops and the status payload records the failure instead of silently falling back.
- Startup now verifies the latest closed K-line window before live monitoring is allowed to run.
- Signal push checks now use a configurable weighted vote ratio instead of requiring every strategy voter to agree.
- Daily learning review runs as a background module after realtime startup, so it is visible in health status without delaying WebSocket connection.

## Runtime Health Signals
- `outputs/latest_scan_status.json` is refreshed on each scan cycle.
- The dashboard runtime panel reads this payload and shows WebSocket connection state, scan freshness, reconnect count, and checked-symbol count.
- A healthy run should show `websocket.connected=true`, `websocket.degraded=false`, and a recent `generated_at`.
- The same payload now includes `modules.closed_kline_backfill`, `modules.signal_closed_bar_gate`, and `modules.daily_learning_review`.
- A healthy signal scan should show closed K-line modules as `healthy`; missing current closed bars are reported as `missing_latest_closed_bar`.

## Vote Gate
- Weighted strategy voters: `trend_breakout` 0.40, `mean_reversion` 0.25, `momentum` 0.20, and `volatility_breakout` 0.15.
- Default minimum support is `strategy.min_vote_approval_rate: 0.40`.
- A candidate can pass with enough same-side support; it does not require unanimous voting.

## WebSocket Behavior
- Default endpoint: `wss://ws.okx.com:8443/ws/v5/business`.
- Default local proxy is used when `127.0.0.1:1088` is available.
- Set `OKX_WS_PROXY=off` to disable WebSocket proxy usage.
- Set `OKX_WS_PROXY=<proxy_url>` to force a specific proxy.

## Verification
- Backend tests: `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m pytest`.
- Dashboard lint: `npm.cmd run lint` from the dashboard directory.
- Dashboard build: `npm.cmd run build` from the dashboard directory.
- Live health check: inspect `outputs/latest_scan_status.json` after startup.

## 2026-06-16 - Task: Enforce Startup Health Gates And Vote Thresholds

### What was done
- Added a startup closed K-line gate for desktop and CLI monitoring. Monitoring does not start if the latest closed candle cannot be verified and backfilled.
- Added runtime module status for closed K-line backfill, signal closed-bar gating, and daily learning review in `latest_scan_status.json` and the dashboard runtime panel.
- Switched signal push gating to a configurable weighted vote ratio through `strategy.min_vote_approval_rate`, defaulting to `0.40`, instead of treating voting as unanimous-only.
- Applied the same vote threshold to live scan, backtest, startup quality gate, daily learning review, and candidate search.
- Moved daily learning review to a background runtime module after realtime startup so it does not delay WebSocket connection.
- Updated package, GUI, CLI, and startup script version to v3.27.

### Testing
- `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m compileall gui.py main.py src`
- `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m pytest tests/test_desktop_runtime.py tests/test_daily_learning_review.py tests/test_backtest.py tests/test_reporting_signal.py`
- `D:\JIAOYI-CX\LOCAL_DEPS\venv\Scripts\python.exe -m pytest`
- `npm.cmd run lint` from `D:\JIAOYI-CX\1_CODE_代码\okx-contract-signal-system\dashboard`
- `npm.cmd run build` from `D:\JIAOYI-CX\1_CODE_代码\okx-contract-signal-system\dashboard`

### Notes
- Changed files include desktop startup/runtime code, CLI startup, backtest/research/training vote-threshold plumbing, Feishu health labels, dashboard runtime status, tests, and version metadata.
- Rollback point: revert commit `fix: enforce startup health gates and vote thresholds`.
