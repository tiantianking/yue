import { execFile } from "node:child_process";
import path from "node:path";
import { promisify } from "node:util";
import { NextRequest, NextResponse } from "next/server";

const execFileAsync = promisify(execFile);

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

function pythonPath() {
  return (
    process.env.OKX_DASHBOARD_PYTHON ??
    "D:\\JIAOYI-CX\\LOCAL_DEPS\\venv\\Scripts\\python.exe"
  );
}

function historyDir() {
  return (
    process.env.OKX_HISTORY_DIR ??
    "D:\\JIAOYI-CX\\历史数据_保留\\lightweight_history\\okx_15m_extended"
  );
}

export async function GET(
  request: NextRequest,
  context: { params: Promise<{ symbol: string }> },
) {
  const { symbol } = await context.params;
  const limit = request.nextUrl.searchParams.get("limit") ?? "260";
  const script = path.join(process.cwd(), "scripts", "read-candles.py");

  try {
    const { stdout } = await execFileAsync(
      pythonPath(),
      [script, decodeURIComponent(symbol), "--limit", limit, "--history-dir", historyDir()],
      {
        maxBuffer: 1024 * 1024 * 8,
        windowsHide: true,
      },
    );
    return new NextResponse(stdout, {
      headers: {
        "content-type": "application/json; charset=utf-8",
        "cache-control": "no-store",
      },
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    return NextResponse.json(
      { error: message, candles: [] },
      { status: 500 },
    );
  }
}
