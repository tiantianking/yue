import assert from "node:assert/strict";
import test from "node:test";
import { historyDir, historyScriptArgs, pythonPath } from "./runtime-paths.ts";

function withEnv<T>(values: Record<string, string | undefined>, fn: () => T): T {
  const previous = new Map<string, string | undefined>();
  for (const key of Object.keys(values)) {
    previous.set(key, process.env[key]);
    if (values[key] === undefined) {
      delete process.env[key];
    } else {
      process.env[key] = values[key];
    }
  }
  try {
    return fn();
  } finally {
    for (const [key, value] of previous.entries()) {
      if (value === undefined) {
        delete process.env[key];
      } else {
        process.env[key] = value;
      }
    }
  }
}

test("pythonPath defaults to python and honors env overrides", () => {
  withEnv({ OKX_DASHBOARD_PYTHON: undefined, PYTHON: undefined }, () => {
    assert.equal(pythonPath(), "python");
  });
  withEnv({ OKX_DASHBOARD_PYTHON: undefined, PYTHON: "py -3.11" }, () => {
    assert.equal(pythonPath(), "py -3.11");
  });
  withEnv({ OKX_DASHBOARD_PYTHON: "custom-python", PYTHON: "python-from-env" }, () => {
    assert.equal(pythonPath(), "custom-python");
  });
});

test("historyScriptArgs lets Python resolve JIAOYI_DATA_DIR and config roots", () => {
  withEnv(
    {
      OKX_HISTORY_DIR: undefined,
      OKX_HISTORY_BASE: undefined,
      JIAOYI_DATA_DIR: "D:\\data\\lightweight_history\\okx_15m_extended",
    },
    () => {
      assert.deepEqual(historyScriptArgs("15m"), ["--dataset", "okx_15m_extended"]);
    },
  );
});

test("historyDir keeps explicit dashboard overrides aligned with Python data-root rules", () => {
  withEnv(
    {
      OKX_HISTORY_DIR: undefined,
      OKX_HISTORY_BASE: "D:\\data\\lightweight_history\\okx_15m_extended",
    },
    () => {
      assert.equal(historyDir("15m"), "D:\\data\\lightweight_history\\okx_15m_extended");
    },
  );
  withEnv(
    {
      OKX_HISTORY_DIR: undefined,
      OKX_HISTORY_BASE: "D:\\data\\lightweight_history",
    },
    () => {
      assert.equal(historyDir("15m"), "D:\\data\\lightweight_history\\okx_15m_extended");
    },
  );
  withEnv(
    {
      OKX_HISTORY_DIR: "D:\\override\\okx_5m_extended",
      OKX_HISTORY_BASE: "D:\\data",
    },
    () => {
      assert.equal(historyDir("5m"), "D:\\override\\okx_5m_extended");
      assert.deepEqual(historyScriptArgs("5m"), ["--history-dir", "D:\\override\\okx_5m_extended"]);
    },
  );
});
