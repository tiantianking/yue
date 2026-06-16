import { readFileSync } from "node:fs";
import path from "node:path";
import { parse as parseYaml } from "yaml";

export function pythonPath() {
  return (
    process.env.OKX_DASHBOARD_PYTHON ??
    "D:\\JIAOYI-CX\\LOCAL_DEPS\\venv\\Scripts\\python.exe"
  );
}

export function historyDir(timeframe = "15m") {
  if (process.env.OKX_HISTORY_DIR) {
    return process.env.OKX_HISTORY_DIR;
  }

  const base =
    process.env.OKX_HISTORY_BASE ?? process.env.JIAOYI_DATA_DIR ?? configDataRoot();
  if (!base) {
    throw new Error(
      "Set OKX_HISTORY_DIR, OKX_HISTORY_BASE, JIAOYI_DATA_DIR, or data.root_dir",
    );
  }

  const historyBase =
    path.basename(base) === "lightweight_history"
      ? base
      : path.join(base, "lightweight_history");
  return path.join(historyBase, `okx_${timeframe}_extended`);
}

function configDataRoot() {
  try {
    const projectRoot = path.resolve(
      process.env.OKX_SIGNAL_ROOT ?? path.join(process.cwd(), ".."),
    );
    const raw = readFileSync(path.join(projectRoot, "config", "base.yaml"), "utf8");
    const config = parseYaml(raw) as { data?: { root_dir?: unknown } } | null;
    return typeof config?.data?.root_dir === "string"
      ? config.data.root_dir
      : undefined;
  } catch {
    return undefined;
  }
}

export function dashboardExecTimeoutMs() {
  const value = Number(process.env.OKX_DASHBOARD_EXEC_TIMEOUT_MS ?? 8000);
  return Number.isFinite(value) ? Math.max(1000, Math.min(value, 30000)) : 8000;
}
