import type { BackfillRow, SymbolRow } from "./types";

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

function toSymbolRow(symbol: string, backfill?: BackfillRow, source?: string): SymbolRow {
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
    source,
  };
}

export function buildSymbolRows({
  configuredSymbols,
  closedRows,
  scanSymbols,
  legacyRows,
  actualBySymbol,
}: {
  configuredSymbols: string[];
  closedRows: BackfillRow[];
  scanSymbols: string[];
  legacyRows: BackfillRow[];
  actualBySymbol: Map<string, Partial<SymbolRow>>;
}): SymbolRow[] {
  const symbols = [
    ...new Set([
      ...configuredSymbols,
      ...closedRows.map((row) => row.inst_id),
      ...scanSymbols,
      ...legacyRows.map((row) => row.inst_id),
    ]),
  ];
  const closedBySymbol = new Map(closedRows.map((row) => [row.inst_id, row]));
  const legacyBySymbol = new Map(legacyRows.map((row) => [row.inst_id, row]));

  return symbols.map((symbol) => {
    const closed = closedBySymbol.get(symbol);
    if (closed) {
      return toSymbolRow(symbol, closed, "closed_kline_backfill_status.json");
    }
    const actual = actualBySymbol.get(symbol);
    if (actual) {
      return {
        ...toSymbolRow(symbol, actual as BackfillRow, "runtime_history_summary"),
        ...actual,
        inst_id: symbol,
        base: symbol.replace("-USDT-SWAP", ""),
        age_minutes: minutesSince(actual.last_ts),
        source: "runtime_history_summary",
      } as SymbolRow;
    }
    const legacy = legacyBySymbol.get(symbol);
    return toSymbolRow(
      symbol,
      legacy,
      legacy ? "15m_backfill_3y_report.json" : "configured_symbol_only",
    );
  });
}
