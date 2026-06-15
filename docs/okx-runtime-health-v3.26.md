# OKX Signal System Runtime Health v3.26

## Business Outcome
- The system no longer reports realtime monitoring as healthy just because a WebSocket thread started.
- Startup health now requires the OKX WebSocket to actually open and stay connected.
- If realtime WebSocket startup fails, monitoring stops and the status payload records the failure instead of silently falling back.

## Runtime Health Signals
- `outputs/latest_scan_status.json` is refreshed on each scan cycle.
- The dashboard runtime panel reads this payload and shows WebSocket connection state, scan freshness, reconnect count, and checked-symbol count.
- A healthy run should show `websocket.connected=true`, `websocket.degraded=false`, and a recent `generated_at`.

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
