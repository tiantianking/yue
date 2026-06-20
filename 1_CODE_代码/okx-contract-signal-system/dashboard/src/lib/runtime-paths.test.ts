import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { historyDir, historyScriptArgs, pythonCommand, pythonPath } from "./runtime-paths.ts";

function withEnv<T>(values: Record<string, string | undefined>, fn: () => T): T {
  const previous = new Map<string, string | undefined>();
  for (const key of Object.keys(values)) {
    previous.set(key, process.env[key]);
    if (values[key] === undefined) delete process.env[key];
    else process.env[key] = values[key];
  }
  try {
    return fn();
  } finally {
    for (const [key, value] of previous.entries()) {
      if (value === undefined) delete process.env[key];
      else process.env[key] = value;
    }
  }
}

function withCwd<T>(cwd: string, fn: () => T): T {
  const previous = process.cwd();
  process.chdir(cwd);
  try {
    return fn();
  } finally {
    process.chdir(previous);
  }
}

test("pythonPath uses workspace Python and explicit override wins", () => {
  const tmp = mkdtempSync(path.join(os.tmpdir(), "okx-dashboard-"));
  const dashboardCwd = path.join(tmp, "workspace", "source", "project", "dashboard");
  const workspacePython = path.join(
    tmp,
    "workspace",
    "LOCAL_DEPS",
    "venv",
    process.platform === "win32" ? "Scripts" : "bin",
    process.platform === "win32" ? "python.exe" : "python",
  );
  mkdirSync(path.dirname(workspacePython), { recursive: true });
  mkdirSync(dashboardCwd, { recursive: true });
  writeFileSync(workspacePython, "");
  withCwd(dashboardCwd, () => withEnv({ OKX_DASHBOARD_PYTHON: undefined, PYTHON: undefined }, () => {
    assert.equal(pythonPath(), workspacePython);
  }));
  withCwd(dashboardCwd, () => withEnv({ OKX_DASHBOARD_PYTHON: "custom-python", PYTHON: "python-from-env" }, () => {
    assert.equal(pythonPath(), "custom-python");
  }));
});

test("pythonCommand separates Windows launcher arguments", () => {
  const tmp = mkdtempSync(path.join(os.tmpdir(), "okx-dashboard-"));
  withCwd(tmp, () => withEnv({ OKX_DASHBOARD_PYTHON: undefined, PYTHON: "py -3.11" }, () => {
    assert.deepEqual(pythonCommand(), { executable: "py", prefixArgs: ["-3.11"] });
  }));
});

test("pythonCommand preserves quoted executable paths containing spaces", () => {
  const tmp = mkdtempSync(path.join(os.tmpdir(), "okx-dashboard-"));
  withCwd(tmp, () => withEnv({
    OKX_DASHBOARD_PYTHON: '"C:\\Program Files\\Python311\\python.exe"',
    PYTHON: undefined,
  }, () => {
    assert.deepEqual(pythonCommand(), {
      executable: "C:\\Program Files\\Python311\\python.exe",
      prefixArgs: [],
    });
  }));
});

test("historyScriptArgs lets Python resolve configured data roots", () => {
  withEnv(
    {
      OKX_HISTORY_DIR: undefined,
      OKX_HISTORY_BASE: undefined,
      JIAOYI_DATA_DIR: "D:\\data\\lightweight_history\\okx_15m_extended",
    },
    () => assert.deepEqual(historyScriptArgs("15m"), ["--dataset", "okx_15m_extended"]),
  );
});

test("historyDir resolves Windows, UNC and POSIX roots", () => {
  withEnv({ OKX_HISTORY_DIR: undefined, OKX_HISTORY_BASE: "D:\\data" }, () => {
    assert.equal(historyDir("15m"), "D:\\data\\lightweight_history\\okx_15m_extended");
  });
  withEnv({ OKX_HISTORY_DIR: undefined, OKX_HISTORY_BASE: "\\\\nas\\share\\data" }, () => {
    assert.equal(historyDir("15m"), "\\\\nas\\share\\data\\lightweight_history\\okx_15m_extended");
  });
  withEnv({ OKX_HISTORY_DIR: undefined, OKX_HISTORY_BASE: "/mnt/data" }, () => {
    assert.equal(historyDir("5m"), "/mnt/data/lightweight_history/okx_5m_extended");
  });
});

test("historyDir preserves explicit dataset and lightweight-history roots", () => {
  withEnv({ OKX_HISTORY_DIR: undefined, OKX_HISTORY_BASE: "/mnt/data/lightweight_history/okx_15m_extended" }, () => {
    assert.equal(historyDir("15m"), "/mnt/data/lightweight_history/okx_15m_extended");
  });
  withEnv({ OKX_HISTORY_DIR: undefined, OKX_HISTORY_BASE: "/mnt/data/lightweight_history" }, () => {
    assert.equal(historyDir("15m"), "/mnt/data/lightweight_history/okx_15m_extended");
  });
  withEnv({ OKX_HISTORY_DIR: "D:\\override\\okx_5m_extended", OKX_HISTORY_BASE: "D:\\data" }, () => {
    assert.equal(historyDir("5m"), "D:\\override\\okx_5m_extended");
    assert.deepEqual(historyScriptArgs("5m"), ["--history-dir", "D:\\override\\okx_5m_extended"]);
  });
});

test("pythonPath resolves the actual workspace interpreter when present", () => {
  const currentWorkspacePython = path.resolve(
    process.cwd(),
    "..",
    "..",
    "..",
    "LOCAL_DEPS",
    "venv",
    process.platform === "win32" ? "Scripts" : "bin",
    process.platform === "win32" ? "python.exe" : "python",
  );
  withEnv({ OKX_DASHBOARD_PYTHON: undefined, PYTHON: undefined }, () => {
    if (pythonPath() !== "python") assert.equal(pythonPath(), currentWorkspacePython);
  });
});
