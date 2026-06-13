import { execFile } from "node:child_process";
import path from "node:path";
import { promisify } from "node:util";
import { NextRequest, NextResponse } from "next/server";
import { dashboardExecTimeoutMs, historyDir, pythonPath } from "@/lib/runtime-paths";

const execFileAsync = promisify(execFile);
const CANDLE_CACHE_TTL_MS = 10_000;
const candleCache = new Map<string, { expiresAt: number; body: string }>();

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

function normalizeLimit(value: string | null) {
  const parsed = Number(value ?? 260);
  if (!Number.isFinite(parsed)) return 260;
  return Math.max(20, Math.min(Math.trunc(parsed), 60000));
}

function normalizeTimeframe(value: string | null) {
  const timeframe = String(value ?? "15m").trim().toLowerCase();
  return timeframe === "5m" || timeframe === "15m" ? timeframe : "15m";
}

export async function GET(
  request: NextRequest,
  context: { params: Promise<{ symbol: string }> },
) {
  const { symbol } = await context.params;
  const limit = normalizeLimit(request.nextUrl.searchParams.get("limit"));
  const timeframe = normalizeTimeframe(request.nextUrl.searchParams.get("timeframe"));
  const script = path.join(process.cwd(), "scripts", "read-candles.py");
  const decodedSymbol = decodeURIComponent(symbol);
  const cacheKey = `${decodedSymbol}|${timeframe}|${limit}`;
  const cached = candleCache.get(cacheKey);
  if (cached && cached.expiresAt > Date.now()) {
    return new NextResponse(cached.body, {
      headers: {
        "content-type": "application/json; charset=utf-8",
        "cache-control": "no-store",
        "x-cache": "hit",
      },
    });
  }

  try {
    const { stdout } = await execFileAsync(
      pythonPath(),
      [
        script,
        decodedSymbol,
        "--limit",
        String(limit),
        "--timeframe",
        timeframe,
        "--history-dir",
        historyDir(timeframe),
      ],
      {
        maxBuffer: 1024 * 1024 * 8,
        windowsHide: true,
        timeout: dashboardExecTimeoutMs(),
      },
    );
    candleCache.set(cacheKey, {
      expiresAt: Date.now() + CANDLE_CACHE_TTL_MS,
      body: stdout,
    });
    return new NextResponse(stdout, {
      headers: {
        "content-type": "application/json; charset=utf-8",
        "cache-control": "no-store",
        "x-cache": "miss",
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
