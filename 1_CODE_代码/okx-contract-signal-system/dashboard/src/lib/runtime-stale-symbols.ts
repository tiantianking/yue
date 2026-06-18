type RuntimeSymbolRow = {
  inst_id: string;
  status?: string;
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

const SYMBOL_STALE_MINUTES = 90;

function uniqueSymbols(values: string[]): string[] {
  return [...new Set(values.map((value) => value.trim()).filter(Boolean))];
}

function stringArray(value: unknown): string[] {
  return Array.isArray(value) ? uniqueSymbols(value.map(String)) : [];
}

function runtimeStaleSymbols(symbolRows: RuntimeSymbolRow[]): string[] {
  return uniqueSymbols(
    symbolRows
      .filter((row) => {
        if (row.status !== "passed" || Boolean(row.error?.trim())) {
          return true;
        }
        if (typeof row.age_minutes !== "number" || !Number.isFinite(row.age_minutes)) {
          return true;
        }
        return row.age_minutes > SYMBOL_STALE_MINUTES;
      })
      .map((row) => row.inst_id),
  );
}

function backfillStaleSymbols(closedBackfill?: ClosedBackfillLike | null): string[] {
  if (!Array.isArray(closedBackfill?.symbols)) {
    return [];
  }
  return uniqueSymbols(
    closedBackfill.symbols
      .filter((row) => {
        if (!row.inst_id) {
          return false;
        }
        if (row.status !== "passed" || Boolean(row.error?.trim())) {
          return true;
        }
        if (typeof row.missing_closed_bars === "number" && row.missing_closed_bars > 0) {
          return true;
        }
        return row.data_complete === false;
      })
      .map((row) => String(row.inst_id)),
  );
}

export function dashboardStaleSymbols(
  symbolRows: RuntimeSymbolRow[],
  quality: JsonRecord,
  runtimeOperational: boolean,
  closedBackfill?: ClosedBackfillLike | null,
  closedBackfillFresh: boolean = false,
): string[] {
  const startupStale = stringArray(quality.stale_symbols);
  const runtimeStale = runtimeStaleSymbols(symbolRows);
  const backfillStale = backfillStaleSymbols(closedBackfill);

  if (
    runtimeOperational &&
    closedBackfillFresh &&
    closedBackfill?.all_complete === true &&
    Array.isArray(closedBackfill.symbols)
  ) {
    const authoritativeSymbols = new Set(
      closedBackfill.symbols
        .map((row) => String(row.inst_id ?? "").trim())
        .filter(Boolean),
    );
    const supplementalRuntimeStale = runtimeStaleSymbols(
      symbolRows.filter((row) => !authoritativeSymbols.has(row.inst_id)),
    );
    return uniqueSymbols([...backfillStale, ...supplementalRuntimeStale]);
  }

  if (runtimeOperational) {
    return uniqueSymbols([...runtimeStale, ...backfillStale]);
  }

  return uniqueSymbols([...startupStale, ...runtimeStale, ...backfillStale]);
}
