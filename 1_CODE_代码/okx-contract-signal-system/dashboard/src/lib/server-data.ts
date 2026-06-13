import { execFile } from "node:child_process";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { promisify } from "node:util";
import { parse as parseYaml } from "yaml";
import type {
  BackfillRow,
  ClosedBackfillStatus,
  DashboardPayload,
  JsonRecord,
  LatestSignal,
  StrategyParams,
  SummaryMetrics,
  SymbolRow,
} from "./types";

const execFileAsync = promisify(execFile);

export const projectRoot = path.resolve(
  process.env.OKX_SIGNAL_ROOT ?? path.join(process.cwd(), ".."),
);

const outputsDir = path.join(projectRoot, "outputs");
const configDir = path.join(projectRoot, "config");

function pythonPath() {
  return (
    process.env.OKX_DASHBOARD_PYTHON ??
    "D:\\JIAOYI-CX\\LOCAL_DEPS\\venv\\Scripts\\python.exe"
  );
}

function historyDir(timeframe = "15m") {
  if (process.env.OKX_HISTORY_DIR) {
    return process.env.OKX_HISTORY_DIR;
  }
  const base =
    process.env.OKX_HISTORY_BASE ??
    "D:\\JIAOYI-CX\\历史数据_保留\\lightweight_history";
  return path.join(base, `okx_${timeframe}_extended`);
}

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

async function readActualHistory(symbols: string[]) {
  if (symbols.length === 0) {
    return new Map<string, Partial<SymbolRow>>();
  }
  try {
    const script = path.join(process.cwd(), "scripts", "read-history-summary.py");
    const { stdout } = await execFileAsync(
      pythonPath(),
      [script, "--history-dir", historyDir("15m"), "--timeframe", "15m", ...symbols],
      {
        maxBuffer: 1024 * 1024 * 8,
        windowsHide: true,
      },
    );
    const payload = JSON.parse(stdout) as { symbols?: Partial<SymbolRow>[] };
    const rows = Array.isArray(payload.symbols) ? payload.symbols : [];
    return new Map(
      rows
        .filter((row) => typeof row.inst_id === "string")
        .map((row) => [String(row.inst_id), row]),
    );
  } catch {
    return new Map<string, Partial<SymbolRow>>();
  }
}

export async function loadDashboardData(): Promise<DashboardPayload> {
  const [quality, selectedParams, latestSignal, backfill, closedBackfill, baseConfig, riskConfig] =
    await Promise.all([
      readJson<JsonRecord>(
        path.join(outputsDir, "startup_quality_gate.json"),
        {},
      ),
      readJson<StrategyParams>(path.join(outputsDir, "selected_params.json"), {}),
      readJson<LatestSignal | null>(
        path.join(outputsDir, "latest_signal.json"),
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
      readYaml<JsonRecord>(path.join(configDir, "base.yaml"), {}),
      readYaml<JsonRecord>(path.join(configDir, "risk.yaml"), {}),
    ]);

  const dataConfig = asRecord(baseConfig.data);
  const configuredSymbols = Array.isArray(dataConfig.symbols)
    ? (dataConfig.symbols.filter((item) => typeof item === "string") as string[])
    : [];
  const backfillSymbols = backfill.map((row) => row.inst_id);
  const symbols = [...new Set([...configuredSymbols, ...backfillSymbols])];
  const backfillBySymbol = new Map(backfill.map((row) => [row.inst_id, row]));
  const actualBySymbol = await readActualHistory(symbols);
  const symbolRows = symbols.map((symbol) => {
    const row = toSymbolRow(symbol, backfillBySymbol.get(symbol));
    const actual = actualBySymbol.get(symbol);
    if (!actual) {
      return row;
    }
    return {
      ...row,
      ...actual,
      age_minutes: minutesSince(actual.last_ts ?? row.last_ts),
    };
  });

  return {
    generated_at:
      typeof quality.generated_at === "string"
        ? quality.generated_at
        : new Date().toISOString(),
    project_root: projectRoot,
    signal_timeframe:
      String(quality.signal_timeframe ?? dataConfig.timeframe ?? "15m"),
    trend_timeframe:
      String(quality.trend_timeframe ?? dataConfig.trend_timeframe ?? "1h"),
    dataset: String(quality.dataset ?? dataConfig.historical_dataset ?? "-"),
    symbols: symbolRows,
    quality: {
      status: String(quality.status ?? "unknown"),
      push_allowed: Boolean(quality.push_allowed),
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
    latest_signal: latestSignal,
    closed_backfill: closedBackfill,
  };
}
