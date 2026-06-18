type RuntimeSymbolRow = {
  inst_id: string;
  status: string;
  error?: string;
  age_minutes?: number | null;
};

type ClosedBackfillLike = {
  all_complete?: boolean;
  symbols?: Array<{
    inst_id?: string;
    status?: string;
    missing_closed_bars?: number;
    data_complete?: boolean;
    error?: string;
  }>;
};

type JsonRecord = Record<string, unknown>;

function stringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.map(String) : [];
}

export function dashboardStaleSymbols(
  symbolRows: RuntimeSymbolRow[],
  quality: JsonRecord,
  runtimeOperational: boolean,
  closedBackfill?: ClosedBackfillLike | null,
): string[] {
  if (!runtimeOperational) {
    return stringArray(quality.stale_symbols);
  }
  if (Array.isArray(closedBackfill?.symbols)) {
    return closedBackfill.symbols
      .filter((row) => {
        if (!row.inst_id) {
          return false;
        }
        if (row.status !== "passed" || row.error) {
          return true;
        }
        if (typeof row.missing_closed_bars === "number" && row.missing_closed_bars > 0) {
          return true;
        }
        return row.data_complete === false;
      })
      .map((row) => String(row.inst_id));
  }
  return symbolRows
    .filter((row) => {
      if (row.status !== "passed" || row.error) {
        return true;
      }
      return typeof row.age_minutes === "number" && row.age_minutes > 90;
    })
    .map((row) => row.inst_id);
}
