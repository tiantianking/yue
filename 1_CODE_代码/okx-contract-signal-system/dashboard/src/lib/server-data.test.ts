import assert from "node:assert/strict";
import test from "node:test";
import { buildSymbolRows } from "./symbol-rows.ts";
import { enrichLatestScan, isClosedBackfillFresh } from "./runtime-health.ts";
import {
  resolveManifestReason,
  runtimeBlockingReasons,
  runtimeHealthBlockingReasons,
  runtimeOperationalReady,
  runtimePushAllowed,
} from "./runtime-quality.ts";
import { dashboardStaleSymbols } from "./runtime-stale-symbols.ts";

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

test("online runtime stale symbols come from runtime symbol rows", () => {
  const freshRows = buildSymbolRows({
    configuredSymbols: ["BTC-USDT-SWAP"],
    closedRows: [
      {
        inst_id: "BTC-USDT-SWAP",
        status: "passed",
        rows_after: 345,
        last_ts: new Date().toISOString(),
      },
    ],
    scanSymbols: ["BTC-USDT-SWAP"],
    legacyRows: [],
    actualBySymbol: new Map(),
  });

  assert.deepEqual(
    dashboardStaleSymbols(
      freshRows,
      { stale_symbols: ["BTC-USDT-SWAP"] },
      true,
    ),
    [],
  );
});

test("fresh closed backfill rows override stale row age in dashboard stale symbols", () => {
  assert.deepEqual(
    dashboardStaleSymbols(
      [
        {
          inst_id: "BTC-USDT-SWAP",
          status: "passed",
          age_minutes: 180,
        },
      ],
      { stale_symbols: ["BTC-USDT-SWAP"] },
      true,
      {
        all_complete: true,
        symbols: [
          {
            inst_id: "BTC-USDT-SWAP",
            status: "passed",
            missing_closed_bars: 0,
            data_complete: true,
          },
        ],
      },
      true,
    ),
    [],
  );
});

test("failed closed backfill rows are stale even when runtime is online", () => {
  assert.deepEqual(
    dashboardStaleSymbols(
      [],
      {},
      true,
      {
        all_complete: false,
        symbols: [
          {
            inst_id: "ETH-USDT-SWAP",
            status: "failed",
            missing_closed_bars: 2,
            data_complete: false,
            error: "gap_unrepaired",
          },
        ],
      },
    ),
    ["ETH-USDT-SWAP"],
  );
});

test("closed backfill health does not falsify the actual runtime push permission", () => {
  const inputs = {
    runtimeOperational: true,
    closedBackfillOperational: false,
    latestScanPushAllowed: true,
    manifestReason: "approved_manifest_valid",
  };
  assert.equal(runtimePushAllowed(inputs), true);
  assert.equal(runtimeOperationalReady(inputs), false);
  assert.deepEqual(runtimeBlockingReasons(inputs), []);
  assert.deepEqual(runtimeHealthBlockingReasons(inputs), ["closed_backfill_incomplete"]);
});

test("offline runtime combines startup diagnostics with current failures", () => {
  assert.deepEqual(
    dashboardStaleSymbols(
      [
        {
          inst_id: "ETH-USDT-SWAP",
          status: "failed",
          error: "internal_gap",
          age_minutes: 2,
        },
      ],
      { stale_symbols: ["BTC-USDT-SWAP"] },
      false,
    ),
    ["BTC-USDT-SWAP", "ETH-USDT-SWAP"],
  );
});

test("online passed symbol without a valid timestamp remains stale without fresh authority", () => {
  assert.deepEqual(
    dashboardStaleSymbols(
      [{ inst_id: "BTC-USDT-SWAP", status: "passed", age_minutes: null }],
      {},
      true,
    ),
    ["BTC-USDT-SWAP"],
  );
});

test("stale closed backfill cannot override stale runtime symbol age", () => {
  assert.deepEqual(
    dashboardStaleSymbols(
      [{ inst_id: "BTC-USDT-SWAP", status: "passed", age_minutes: 180 }],
      {},
      true,
      {
        all_complete: true,
        symbols: [
          {
            inst_id: "BTC-USDT-SWAP",
            status: "passed",
            missing_closed_bars: 0,
            data_complete: true,
          },
        ],
      },
      false,
    ),
    ["BTC-USDT-SWAP"],
  );
});

test("fresh authoritative backfill does not hide configured symbols missing from it", () => {
  assert.deepEqual(
    dashboardStaleSymbols(
      [
        { inst_id: "BTC-USDT-SWAP", status: "passed", age_minutes: 180 },
        { inst_id: "ETH-USDT-SWAP", status: "unknown", age_minutes: null },
      ],
      {},
      true,
      {
        all_complete: true,
        symbols: [
          {
            inst_id: "BTC-USDT-SWAP",
            status: "passed",
            missing_closed_bars: 0,
            data_complete: true,
          },
        ],
      },
      true,
    ),
    ["ETH-USDT-SWAP"],
  );
});

test("manifest reason resolution does not turn approval into a blocking reason", () => {
  assert.equal(
    resolveManifestReason({
      manifestStatusReason: undefined,
      latestScanPresent: true,
      latestScanPushAllowed: true,
    }),
    "approved_manifest_valid",
  );
  assert.deepEqual(
    runtimeBlockingReasons({
      runtimeOperational: true,
      closedBackfillOperational: false,
      latestScanPushAllowed: true,
      manifestReason: "approved_manifest_valid",
    }),
    [],
  );
});

test("inconsistent denied push cannot report an approved manifest as the blocker", () => {
  assert.deepEqual(
    runtimeBlockingReasons({
      runtimeOperational: true,
      closedBackfillOperational: true,
      latestScanPushAllowed: false,
      manifestReason: "approved_manifest_valid",
    }),
    ["runtime_manifest_not_approved"],
  );
});
