import path from "node:path";

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
    process.env.OKX_HISTORY_BASE ??
    "D:\\JIAOYI-CX\\历史数据_保留\\lightweight_history";
  return path.join(base, `okx_${timeframe}_extended`);
}

export function dashboardExecTimeoutMs() {
  const value = Number(process.env.OKX_DASHBOARD_EXEC_TIMEOUT_MS ?? 8000);
  return Number.isFinite(value) ? Math.max(1000, Math.min(value, 30000)) : 8000;
}

