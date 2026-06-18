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

const execFileAsync = promisify(execFile);
const SCAN_STALE_MINUTES = 20;
const WS_STALE_MINUTES = 10;

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

function selectedParamsFromManifest(manifest: JsonRecord): StrategyParams {
  return asRecord(manifest.selected_params) as StrategyParams;
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

function minutesSinceEpochSeconds(value?: number | null) {
  if (typeof value !== "number" || !Number.isFinite(value) || value <= 0) {
    return null;
  }
  return Math.max(0, (Date.now() - value * 1000) / 60000);
}

function enrichLatestScan(latestScan: LatestScanStatus | null): LatestScanStatus | null {
  if (!latestScan) {
    return null;
  }
  const ageMinutes = minutesSince(latestScan.generated_at);
  const ws = latestScan.websocket ?? null;
  const wsMessageAge = minutesSinceEpochSeconds(ws?.last_message_at);
  let runtimeStatus = "online";
  let runtimeReason = "live_status_fresh";

  if (latestScan.error) {
    runtimeStatus = "error";
    runtimeReason = "scan_error";
  } else if (typeof ageMinutes === "number" && ageMinutes > SCAN_STALE_MINUTES) {
    runtimeStatus = "stale";
    runtimeReason = "scan_status_stale";
  } else if (!ws?.running || !ws?.connected) {
    runtimeStatus = "offline";
    runtimeReason = "websocket_offline";
  } else if (ws?.degraded) {
    runtimeStatus = "stale";
    runtimeReason = "websocket_degraded";
  } else if (typeof wsMessageAge === "number" && wsMessageAge > WS_STALE_MINUTES) {
    runtimeStatus = "stale";
    runtimeReason = "websocket_message_stale";
  }

  return {
    ...latestScan,
    runtime_status: runtimeStatus,
    runtime_reason: runtimeReason,
    age_minutes: ageMinutes,
    websocket: ws
      ? {
          ...ws,
          last_message_age_minutes: wsMessageAge,
        }
      : ws,
  };
}

function toSymbolRow(symbol: string, backfill?: BackfillRow): SymbolRow {
  return {
    inst_id: symbol,
    base: symbol.replace("-USDT-SWAP", ""),
    status: backfill?.status ?? "unknown",
    rows_after: Number(backfill?.rows_after ?? 0),
    added_rows: Number(backfill?.added_rows ?? 0),
    first_ts: backfill?.first_ts ?? "",
    last_ts: backfill?.last_ts ?? "",
    age_minutes: minutesSince(backfill?.last_ts),
    error: backfill?.error ?? "",
  };
}

function latestScanSymbols(latestScan: LatestScanStatus | null): string[] {
  const rows = Array.isArray(latestScan?.symbols) ? latestScan.symbols : [];
  return rows
    .map((row) => row.symbol)
    .filter((symbol): symbol is string => typeof symbol === "string" && symbol.length > 0);
}

function closedBackfillMap(status: ClosedBackfillStatus | null) {
  return new Map((status?.symbols ?? []).map((row) => [row.inst_id, row]));
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
    approvedManifest,
    legacySelectedParams,
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
      readJson<JsonRecord>(
        path.join(outputsDir, "runtime", "approved_strategy_manifest.json"),
        {},
      ),
      readJson<StrategyParams>(path.join(outputsDir, "selected_params.json"), {}),
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
  const selectedParams = {
    ...legacySelectedParams,
    ...selectedParamsFromManifest(approvedManifest),
  };
  const configuredSymbols = Array.isArray(dataConfig.symbols)
    ? (dataConfig.symbols.filter((item) => typeof item === "string") as string[])
    : [];
  const backfillSymbols = backfill.map((row) => row.inst_id);
  const enrichedLatestScan = enrichLatestScan(latestScan);
  const scanSymbols = latestScanSymbols(enrichedLatestScan);
  const closedSymbols = (closedBackfill?.symbols ?? []).map((row) => row.inst_id);
  const symbols = [...new Set([...configuredSymbols, ...scanSymbols, ...closedSymbols, ...backfillSymbols])];
  const backfillBySymbol = new Map(backfill.map((row) => [row.inst_id, row]));
  const closedBySymbol = closedBackfillMap(closedBackfill);
  const actualBySymbol = await readActualHistory(symbols);
  const effectiveLatestSignal = enrichedLatestScan
    ? enrichedLatestScan.last_signal ?? null
    : latestSignal;
  const symbolRows = symbols.map((symbol) => {
    const closed = closedBySymbol.get(symbol);
    const row = toSymbolRow(symbol, backfillBySymbol.get(symbol));
    const actual = actualBySymbol.get(symbol);
    const merged = {
      ...row,
      ...(closed
        ? {
            status: closed.status,
            rows_after: Number(closed.rows_after ?? row.rows_after),
            added_rows: Number(closed.added_rows ?? row.added_rows),
            first_ts: closed.first_ts ?? row.first_ts,
            last_ts: closed.last_ts ?? row.last_ts,
            error: closed.error ?? row.error,
          }
        : {}),
      ...(actual ?? {}),
    };
    return {
      ...merged,
      age_minutes: minutesSince(merged.last_ts),
    };
  });

  return {
    generated_at: new Date().toISOString(),
    project_root: projectRoot,
    signal_timeframe:
      String(quality.signal_timeframe ?? dataConfig.timeframe ?? "15m"),
    trend_timeframe:
      String(quality.trend_timeframe ?? dataConfig.trend_timeframe ?? "1h"),
    dataset: String(quality.dataset ?? dataConfig.historical_dataset ?? "-"),
    symbols: symbolRows,
    quality: {
      status: String(quality.status ?? "unknown"),
      push_allowed: Boolean(enrichedLatestScan?.push_allowed ?? quality.push_allowed),
      reasons: Array.isArray(quality.reasons)
        ? quality.reasons.map(String)
        : [],
      push_blocking_reasons: Array.isArray(quality.push_blocking_reasons)
        ? quality.push_blocking_reasons.map(String)
        : [],
      stale_symbols: Array.isArray(quality.stale_symbols)
        ? quality.stale_symbols.map(String)
        : [],
    },
    train_summary: asRecord(quality.train_summary) as SummaryMetrics,
    valid_summary: asRecord(quality.valid_summary) as SummaryMetrics,
    stress_checks: asRecord(quality.stress_checks),
    selected_params: {
      ...selectedParams,
      ...(asRecord(quality.selected_params) as StrategyParams),
    },
    risk_config: asRecord(riskConfig.risk),
    latest_signal: effectiveLatestSignal,
    latest_scan: enrichedLatestScan,
    closed_backfill: closedBackfill,
    closed_backfills: {
      "15m": closedBackfill,
      "5m": closedBackfill5m,
    },
    learning_review: learningReview,
  };
}
