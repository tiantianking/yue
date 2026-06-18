import { execFile } from "node:child_process";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { promisify } from "node:util";
import { parse as parseYaml } from "yaml";
import type {
  BackfillRow,
  ClosedBackfillStatus,
  DailyLearningReviewStatus,
  DashboardPayload,
  JsonRecord,
  LatestScanStatus,
  LatestSignal,
  StrategyParams,
  SummaryMetrics,
  SymbolRow,
} from "./types";
import { dashboardExecTimeoutMs, historyScriptArgs, pythonPath } from "./runtime-paths";
import { enrichLatestScan } from "./runtime-health";
import { dashboardStaleSymbols } from "./runtime-stale-symbols";
import { buildSymbolRows } from "./symbol-rows";

const execFileAsync = promisify(execFile);

export const projectRoot = path.resolve(
  process.env.OKX_SIGNAL_ROOT ?? path.join(process.cwd(), ".."),
);

const outputsDir = path.join(projectRoot, "outputs");
const configDir = path.join(projectRoot, "config");
const HISTORY_CACHE_TTL_MS = 5000;

let actualHistoryCache:
  | { key: string; expiresAt: number; value: Map<string, Partial<SymbolRow>> }
  | null = null;

async function readJson<T>(filePath: string, fallback: T): Promise<T> {
  try {
    const raw = await readFile(filePath, "utf8");
    return JSON.parse(raw) as T;
  } catch {
    return fallback;
  }
}

async function readYaml<T>(filePath: string, fallback: T): Promise<T> {
  try {
    const raw = await readFile(filePath, "utf8");
    return parseYaml(raw) as T;
  } catch {
    return fallback;
  }
}

function asRecord(value: unknown): JsonRecord {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as JsonRecord)
    : {};
}

function minutesSince(value?: string) {
  if (!value) {
    return null;
  }
  const ts = new Date(value).getTime();
  if (Number.isNaN(ts)) {
    return null;
  }
  return Math.max(0, (Date.now() - ts) / 60000);
}

function secondsSince(value?: string) {
  const minutes = minutesSince(value);
  return typeof minutes === "number" ? minutes * 60 : null;
}

async function readActualHistory(symbols: string[]) {
  if (symbols.length === 0) {
    return new Map<string, Partial<SymbolRow>>();
  }
  const cacheKey = symbols.join("|");
  if (
    actualHistoryCache &&
    actualHistoryCache.key === cacheKey &&
    actualHistoryCache.expiresAt > Date.now()
  ) {
    return actualHistoryCache.value;
  }
  try {
    const script = path.join(process.cwd(), "scripts", "read-history-summary.py");
    const { stdout } = await execFileAsync(
      pythonPath(),
      [script, "--timeframe", "15m", ...historyScriptArgs("15m"), ...symbols],
      {
        maxBuffer: 1024 * 1024 * 8,
        windowsHide: true,
        timeout: dashboardExecTimeoutMs(),
      },
    );
    const payload = JSON.parse(stdout) as { symbols?: Partial<SymbolRow>[] };
    const rows = Array.isArray(payload.symbols) ? payload.symbols : [];
    const value = new Map(
      rows
        .filter((row) => typeof row.inst_id === "string")
        .map((row) => [String(row.inst_id), row]),
    );
    actualHistoryCache = {
      key: cacheKey,
      expiresAt: Date.now() + HISTORY_CACHE_TTL_MS,
      value,
    };
    return value;
  } catch {
    return new Map<string, Partial<SymbolRow>>();
  }
}

export async function loadDashboardData(): Promise<DashboardPayload> {
  const [
    quality,
    latestSignal,
    latestScan,
    backfill,
    closedBackfill,
    closedBackfill5m,
    learningReview,
    baseConfig,
    riskConfig,
  ] =
    await Promise.all([
      readJson<JsonRecord>(
        path.join(outputsDir, "startup_quality_gate.json"),
        {},
      ),
      readJson<LatestSignal | null>(
        path.join(outputsDir, "latest_signal.json"),
        null,
      ),
      readJson<LatestScanStatus | null>(
        path.join(outputsDir, "latest_scan_status.json"),
        null,
      ),
      readJson<BackfillRow[]>(
        path.join(outputsDir, "15m_backfill_3y_report.json"),
        [],
      ),
      readJson<ClosedBackfillStatus | null>(
        path.join(outputsDir, "closed_kline_backfill_status.json"),
        null,
      ),
      readJson<ClosedBackfillStatus | null>(
        path.join(outputsDir, "closed_kline_backfill_status_5m.json"),
        null,
      ),
      readJson<DailyLearningReviewStatus | null>(
        path.join(outputsDir, "daily_learning_review.json"),
        null,
      ),
      readYaml<JsonRecord>(path.join(configDir, "base.yaml"), {}),
      readYaml<JsonRecord>(path.join(configDir, "risk.yaml"), {}),
    ]);

  const dataConfig = asRecord(baseConfig.data);
  const configuredSymbols = Array.isArray(dataConfig.symbols)
    ? (dataConfig.symbols.filter((item) => typeof item === "string") as string[])
    : [];
  const closedRows = Array.isArray(closedBackfill?.symbols)
    ? closedBackfill.symbols.map((row) => ({ ...row, inst_id: String(row.inst_id) } as BackfillRow))
    : [];
  const scanSymbols = Array.isArray(latestScan?.symbols)
    ? latestScan.symbols
        .map((row) => (typeof row.symbol === "string" ? row.symbol : ""))
        .filter(Boolean)
    : [];
  const symbols = [
    ...new Set([
      ...configuredSymbols,
      ...closedRows.map((row) => row.inst_id),
      ...scanSymbols,
      ...backfill.map((row) => row.inst_id),
    ]),
  ];
  const actualBySymbol = await readActualHistory(symbols);
  const enrichedLatestScan = enrichLatestScan(latestScan, closedBackfill, closedBackfill5m);
  const runtimeManifestStatus = asRecord(enrichedLatestScan?.manifest_status);
  const runtimeParams = asRecord(enrichedLatestScan?.selected_params) as StrategyParams;
  const runtimeOperational = enrichedLatestScan?.runtime_status === "online";
  const runtimePushAllowed = runtimeOperational && enrichedLatestScan?.push_allowed === true;
  const manifestReason =
    typeof runtimeManifestStatus.reason === "string"
      ? runtimeManifestStatus.reason
      : runtimePushAllowed
        ? "approved_manifest_valid"
        : enrichedLatestScan
          ? "runtime_manifest_not_approved"
          : "runtime_status_missing";
  const runtimeBlockingReasons = runtimePushAllowed
    ? []
    : [
        runtimeOperational
          ? manifestReason
          : enrichedLatestScan?.runtime_reason ?? "runtime_not_online",
      ];
  const effectiveLatestSignal = enrichedLatestScan
    ? enrichedLatestScan.last_signal ?? null
    : latestSignal;
  const symbolRows = buildSymbolRows({
    configuredSymbols,
    closedRows,
    scanSymbols,
    legacyRows: backfill,
    actualBySymbol,
  });
  const staleSymbols = dashboardStaleSymbols(symbolRows, quality, runtimeOperational);

  return {
    generated_at: new Date().toISOString(),
    runtime_mode: runtimePushAllowed ? "formal_push" : "research_observation",
    runtime_status_source: enrichedLatestScan
      ? "latest_scan_status.json"
      : "latest_scan_status_missing",
    latest_scan_age_seconds: secondsSince(enrichedLatestScan?.generated_at),
    backfill_status_age_seconds: secondsSince(closedBackfill?.generated_at),
    project_root: projectRoot,
    signal_timeframe: String(
      enrichedLatestScan?.signal_timeframe ?? quality.signal_timeframe ?? dataConfig.timeframe ?? "15m",
    ),
    trend_timeframe: String(
      enrichedLatestScan?.trend_timeframe ?? quality.trend_timeframe ?? dataConfig.trend_timeframe ?? "1h",
    ),
    dataset: String(
      enrichedLatestScan?.dataset ?? quality.dataset ?? dataConfig.historical_dataset ?? "-",
    ),
    symbols: symbolRows,
    quality: {
      status: runtimePushAllowed ? "runtime_approved" : "runtime_blocked",
      push_allowed: runtimePushAllowed,
      manifest_reason: manifestReason,
      params_source:
        Object.keys(runtimeParams).length === 0
          ? "none"
          : runtimeManifestStatus.ok === true
            ? "approved_runtime_manifest"
            : "blocked_runtime_snapshot",
      reasons: Array.isArray(quality.reasons)
        ? quality.reasons.map(String)
        : [],
      push_blocking_reasons: [
        ...new Set([
          ...(Array.isArray(quality.push_blocking_reasons)
            ? quality.push_blocking_reasons.map(String)
            : []),
          ...runtimeBlockingReasons,
        ]),
      ],
      stale_symbols: staleSymbols,
    },
    train_summary: asRecord(quality.train_summary) as SummaryMetrics,
    valid_summary: asRecord(quality.valid_summary) as SummaryMetrics,
    stress_checks: asRecord(quality.stress_checks),
    selected_params: runtimeParams,
    risk_config: asRecord(riskConfig.risk),
    latest_signal: effectiveLatestSignal,
    latest_scan: enrichedLatestScan,
    closed_backfill: closedBackfill,
    closed_backfill_5m: closedBackfill5m,
    closed_backfills: {
      "15m": closedBackfill,
      "5m": closedBackfill5m,
    },
    learning_review: learningReview,
  };
}
