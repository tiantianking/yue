import assert from "node:assert/strict";
import test from "node:test";
import { buildSymbolRows } from "./symbol-rows.ts";
import { enrichLatestScan, isClosedBackfillFresh } from "./runtime-health.ts";

test("closed runtime backfill is authoritative over stale history summaries", () => {
  const rows = buildSymbolRows({
    configuredSymbols: ["BTC-USDT-SWAP"],
    closedRows: [
      {
        inst_id: "BTC-USDT-SWAP",
        status: "passed",
        rows_after: 300,
        added_rows: 2,
        first_ts: "2026-06-15T00:00:00Z",
        last_ts: "2026-06-18T05:30:00Z",
      },
    ],
    scanSymbols: ["BTC-USDT-SWAP"],
    legacyRows: [
      {
        inst_id: "BTC-USDT-SWAP",
        status: "unknown",
        rows_after: 0,
        last_ts: "2026-06-16T00:00:00Z",
      },
    ],
    actualBySymbol: new Map([
      [
        "BTC-USDT-SWAP",
        {
          inst_id: "BTC-USDT-SWAP",
          base: "BTC",
          status: "unknown",
          rows_after: 0,
          added_rows: 0,
          first_ts: "",
          last_ts: "2026-06-16T00:00:00Z",
          age_minutes: null,
          error: "",
        },
      ],
    ]),
  });

  assert.equal(rows.length, 1);
  assert.equal(rows[0].status, "passed");
  assert.equal(rows[0].rows_after, 300);
  assert.equal(rows[0].last_ts, "2026-06-18T05:30:00Z");
  assert.equal(rows[0].source, "closed_kline_backfill_status.json");
});

test("configured symbols without runtime evidence remain explicitly sourced", () => {
  const rows = buildSymbolRows({
    configuredSymbols: ["ETH-USDT-SWAP"],
    closedRows: [],
    scanSymbols: [],
    legacyRows: [],
    actualBySymbol: new Map(),
  });

  assert.equal(rows[0].status, "unknown");
  assert.equal(rows[0].source, "configured_symbol_only");
});


test("fresh completed closed backfill keeps connected quiet websocket online", () => {
  const now = Date.now();
  const backfill = {
    all_complete: true,
    generated_at: new Date(now - 60_000).toISOString(),
    next_run_at: new Date(now + 60_000).toISOString(),
  };
  assert.equal(isClosedBackfillFresh(backfill, now), true);

  const result = enrichLatestScan(
    {
      generated_at: new Date(now - 60_000).toISOString(),
      websocket: {
        running: true,
        connected: true,
        degraded: false,
        last_message_at: (now - 20 * 60_000) / 1000,
      },
    },
    backfill,
    null,
  );

  assert.equal(result?.runtime_status, "online");
  assert.equal(result?.runtime_reason, "closed_backfill_fresh_ws_quiet");
});

test("incomplete closed backfill does not hide stale websocket", () => {
  const now = Date.now();
  const result = enrichLatestScan(
    {
      generated_at: new Date(now - 60_000).toISOString(),
      websocket: {
        running: true,
        connected: true,
        degraded: false,
        last_message_at: (now - 20 * 60_000) / 1000,
      },
    },
    {
      all_complete: false,
      generated_at: new Date(now - 60_000).toISOString(),
      next_run_at: new Date(now + 60_000).toISOString(),
    },
    null,
  );

  assert.equal(result?.runtime_status, "stale");
  assert.equal(result?.runtime_reason, "websocket_message_stale");
});
