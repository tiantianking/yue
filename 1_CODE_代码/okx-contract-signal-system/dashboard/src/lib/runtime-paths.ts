import path from "node:path";

export function pythonPath() {
  return process.env.OKX_DASHBOARD_PYTHON ?? process.env.PYTHON ?? "python";
}

function datasetName(timeframe: string) {
  return `okx_${timeframe}_extended`;
}

function pathApiFor(inputPath: string) {
  if (/^[a-zA-Z]:[\\/]/.test(inputPath) || /^[\\/]{2}[^\\/]/.test(inputPath)) {
    return path.win32;
  }
  return path.posix;
}

function datasetUnderDataRoot(dataRoot: string, dataset: string) {
  const pathApi = pathApiFor(dataRoot);
  if (pathApi.basename(dataRoot) === dataset) {
    return dataRoot;
  }
  if (pathApi.basename(dataRoot) === "lightweight_history") {
    return pathApi.join(dataRoot, dataset);
  }
  return pathApi.join(dataRoot, "lightweight_history", dataset);
}

function explicitHistoryDir(timeframe = "15m") {
  if (process.env.OKX_HISTORY_DIR) {
    return process.env.OKX_HISTORY_DIR;
  }
  if (process.env.OKX_HISTORY_BASE) {
    return datasetUnderDataRoot(process.env.OKX_HISTORY_BASE, datasetName(timeframe));
  }
  return null;
}

export function historyDir(timeframe = "15m") {
  const explicitDir = explicitHistoryDir(timeframe);
  if (!explicitDir) {
    throw new Error(
      "No explicit dashboard history directory; let Python resolve JIAOYI_DATA_DIR or config/base.yaml via historyScriptArgs().",
    );
  }
  return explicitDir;
}

export function historyScriptArgs(timeframe = "15m") {
  const explicitDir = explicitHistoryDir(timeframe);
  if (explicitDir) {
    return ["--history-dir", explicitDir];
  }
  return ["--dataset", datasetName(timeframe)];
}

export function dashboardExecTimeoutMs() {
  const value = Number(process.env.OKX_DASHBOARD_EXEC_TIMEOUT_MS ?? 8000);
  return Number.isFinite(value) ? Math.max(1000, Math.min(value, 30000)) : 8000;
}
