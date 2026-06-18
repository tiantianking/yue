const SCAN_STALE_MINUTES = 20;
const WS_STALE_MINUTES = 10;
const CLOSED_BACKFILL_GRACE_MINUTES = 5;

type ClosedBackfillLike = {
  generated_at?: string;
  next_run_at?: string;
  all_complete?: boolean;
};

type WebsocketLike = {
  running?: boolean;
  connected?: boolean;
  degraded?: boolean;
  last_message_at?: number | null;
  last_message_age_minutes?: number | null;
  [key: string]: unknown;
};

type LatestScanLike = {
  generated_at?: string;
  error?: string | null;
  websocket?: WebsocketLike | null;
  [key: string]: unknown;
};

function timestampMs(value?: string) {
  if (!value) {
    return null;
  }
  const ts = new Date(value).getTime();
  return Number.isNaN(ts) ? null : ts;
}

function minutesSince(value?: string) {
  const ts = timestampMs(value);
  return ts === null ? null : Math.max(0, (Date.now() - ts) / 60000);
}

function minutesSinceEpochSeconds(value?: number | null) {
  if (typeof value !== "number" || !Number.isFinite(value) || value <= 0) {
    return null;
  }
  return Math.max(0, (Date.now() - value * 1000) / 60000);
}

export function isClosedBackfillFresh(
  status?: ClosedBackfillLike | null,
  nowMs: number = Date.now(),
): boolean {
  if (!status?.all_complete) {
    return false;
  }
  const nextRunAt = timestampMs(status.next_run_at);
  if (nextRunAt !== null) {
    return nowMs <= nextRunAt + CLOSED_BACKFILL_GRACE_MINUTES * 60000;
  }
  const generatedAt = timestampMs(status.generated_at);
  return generatedAt !== null && nowMs - generatedAt <= SCAN_STALE_MINUTES * 60000;
}

export function enrichLatestScan<T extends LatestScanLike>(
  latestScan: T | null,
  closedBackfill?: ClosedBackfillLike | null,
  closedBackfill5m?: ClosedBackfillLike | null,
): (T & {
  runtime_status: string;
  runtime_reason: string;
  age_minutes: number | null;
  websocket: WebsocketLike | null;
}) | null {
  if (!latestScan) {
    return null;
  }
  const ageMinutes = minutesSince(latestScan.generated_at);
  const ws = latestScan.websocket ?? null;
  const wsMessageAge = minutesSinceEpochSeconds(ws?.last_message_at);
  const closedBackfillFresh =
    isClosedBackfillFresh(closedBackfill) || isClosedBackfillFresh(closedBackfill5m);
  let runtimeStatus = "online";
  let runtimeReason = "live_status_fresh";

  if (latestScan.error) {
    runtimeStatus = "error";
    runtimeReason = "scan_error";
  } else if (typeof ageMinutes === "number" && ageMinutes > SCAN_STALE_MINUTES) {
    runtimeStatus = "stale";
    runtimeReason = "scan_status_stale";
  } else if (!ws?.running || !ws?.connected) {
    runtimeStatus = "offline";
    runtimeReason = "websocket_offline";
  } else if (ws?.degraded) {
    runtimeStatus = "stale";
    runtimeReason = "websocket_degraded";
  } else if (typeof wsMessageAge === "number" && wsMessageAge > WS_STALE_MINUTES) {
    if (closedBackfillFresh) {
      runtimeReason = "closed_backfill_fresh_ws_quiet";
    } else {
      runtimeStatus = "stale";
      runtimeReason = "websocket_message_stale";
    }
  }

  return {
    ...latestScan,
    runtime_status: runtimeStatus,
    runtime_reason: runtimeReason,
    age_minutes: ageMinutes,
    websocket: ws
      ? {
          ...ws,
          last_message_age_minutes: wsMessageAge,
        }
      : ws,
  };
}
