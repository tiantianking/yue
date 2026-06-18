# Runtime Verification

This note records the v3.56.6 runtime-cache integration acceptance boundary for release packages.

Date: 2026-06-18

Scope:
- The system remains a signal-only OKX public-market-data observer and Feishu notification tool.
- Docker is not required for this validation path.
- Runtime history is loaded from the configured/local system data and runtime cache. Startup only needs to repair missing closed candles after the cached tail.

Observed local runtime state:
- Dashboard API responded at `http://127.0.0.1:3001/api/dashboard`.
- The latest scan status was `running`.
- OKX WebSocket status was connected with 21 subscribed swap symbols.
- 15m closed-candle backfill reported complete across the configured symbol set.
- 5m closed-candle runtime-cache backfill reported complete across the configured symbol set.
- SQLite lifecycle/outbox storage was active; delivered notification rows were retained as `SENT`.
- Formal push was blocked when no valid `outputs/runtime/approved_strategy_manifest.json` was present. This is expected fail-closed behavior, not a runtime fault.

Runtime-cache integration requirements:
- Closed candles are authoritative over open runtime-cache tails at the same timestamp.
- A single open tail is allowed in runtime cache while the next closed candle is pending.
- REST closed-candle repair must replace an open tail without letting stale in-memory open rows repollute the repaired cache.
- Dashboard consumers should prefer `closed_backfills["15m"]` and `closed_backfills["5m"]`. The `closed_backfill` and `closed_backfill_5m` fields remain for compatibility with older checks.
- Dashboard runtime health treats fresh completed closed-backfill status as valid market-data liveness evidence when the WebSocket remains connected but the candle stream is quiet between closed bars.

Release validation commands:
- `py -3.12 -m compileall -q src main.py gui.py tests`
- `py -3.12 -m pytest -q`
- `npm.cmd run check` from `dashboard/`
- `git diff --check`

Packaging:
- Build with `py -3.12 scripts/build_release_zip.py --output C:\Users\26492\Desktop\okx-contract-signal-system-v3.56.6-stale-symbol-guard-final.zip`.
- The release package must exclude `.env`, `outputs/`, `output/`, logs, caches, SQLite databases, `node_modules/`, `.next/`, and other local runtime/build artifacts.
- Generate a matching `.sha256` file beside the zip after the package is built.


Integration note:
- This v3.56.6 package keeps the strict approved-manifest semantic gate, experimental daily-learning isolation, continuous outbox draining, write-result/data-completeness separation, UTF-8 dashboard headers, runtime observability, and Dashboard stale-symbol runtime reconciliation from the previously accepted v3.56.5 line.
- The re-uploaded same-name v3.56.3 archive was not byte-identical to the accepted package and was not used as a release baseline.
