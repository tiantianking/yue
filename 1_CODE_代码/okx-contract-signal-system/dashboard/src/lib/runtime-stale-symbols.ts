type RuntimeSymbolRow = {
  inst_id: string;
  status: string;
  error?: string;
  age_minutes?: number | null;
};

type JsonRecord = Record<string, unknown>;

function stringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.map(String) : [];
}

export function dashboardStaleSymbols(
  symbolRows: RuntimeSymbolRow[],
  quality: JsonRecord,
  runtimeOperational: boolean,
): string[] {
  if (!runtimeOperational) {
    return stringArray(quality.stale_symbols);
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
