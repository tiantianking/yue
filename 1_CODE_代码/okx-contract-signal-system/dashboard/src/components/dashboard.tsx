"use client";

import {
  Activity,
  AlertTriangle,
  CalendarRange,
  Clock3,
  Database,
  LineChart,
  RefreshCw,
  Send,
  ShieldCheck,
  Target,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { KlineChart } from "@/components/kline-chart";
import { MetricTile } from "@/components/metric-tile";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ageText, dateTimeText, integerText, numberText, percent } from "@/lib/format";
import type { Candle, DashboardPayload, SymbolRow } from "@/lib/types";
import { cn } from "@/lib/utils";

type CandlePayload = {
  symbol: string;
  timeframe?: string;
  source?: string;
  count: number;
  last_time?: string;
  candles: Candle[];
  error?: string;
};

type CandleMeta = {
  symbol: string;
  timeframe: string;
  source: string;
  count: number;
  last_time?: string;
};

const timeframes = ["15m", "5m"] as const;
const ranges = [
  { label: "7天", days: 7 },
  { label: "30天", days: 30 },
  { label: "90天", days: 90 },
  { label: "1年", days: 365 },
];

const emptyData: DashboardPayload = {
  generated_at: "",
  project_root: "",
  signal_timeframe: "15m",
  trend_timeframe: "15m",
  dataset: "-",
  symbols: [],
  quality: {
    status: "loading",
    push_allowed: false,
    reasons: [],
    push_blocking_reasons: [],
    stale_symbols: [],
  },
  train_summary: {},
  valid_summary: {},
  stress_checks: {},
  selected_params: {},
  risk_config: {},
  latest_signal: null,
  latest_scan: null,
  closed_backfill: null,
  closed_backfills: {},
  learning_review: null,
};

function sideText(side?: string) {
  if (side === "long") return "做多";
  if (side === "short") return "做空";
  return "空仓";
}

function minutesSince(value?: string) {
  if (!value) return null;
  const ts = new Date(value).getTime();
  if (Number.isNaN(ts)) return null;
  return Math.max(0, (Date.now() - ts) / 60000);
}

function rowFresh(row?: SymbolRow) {
  if (!row || row.status !== "passed") return "red";
  if (typeof row.age_minutes === "number" && row.age_minutes > 90) return "amber";
  return "green";
}

function statusTone(status?: string) {
  if (status === "passed" || status === "green") return "green";
  if (status === "failed" || status === "error") return "red";
  return "amber";
}

function limitFor(days: number, timeframe: string) {
  const minutes = timeframe === "5m" ? 5 : 15;
  return Math.min(60000, Math.ceil((days * 24 * 60) / minutes));
}

function MetricGrid({ data }: { data: DashboardPayload }) {
  const valid = data.valid_summary;
  const train = data.train_summary;
  return (
    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
      <MetricTile
        label="验证盈亏比"
        value={numberText(valid.payoff_ratio, 2)}
        hint={`PF ${numberText(valid.profit_factor, 3)}`}
        tone={(valid.profit_factor ?? 0) >= 1 ? "green" : "amber"}
      />
      <MetricTile
        label="验证胜率"
        value={percent(valid.win_rate, 1)}
        hint={`${integerText(valid.total_trades)} 笔`}
      />
      <MetricTile
        label="最大回撤"
        value={percent(valid.max_drawdown, 2)}
        hint={`训练 ${percent(train.max_drawdown, 2)}`}
        tone={(valid.max_drawdown ?? 1) <= 0.1 ? "green" : "amber"}
      />
      <MetricTile
        label="目标 R"
        value={`${numberText(data.selected_params.take_profit_mult, 1)}R`}
        hint={`止损 ${numberText(data.selected_params.atr_stop_mult, 1)} ATR`}
        tone="green"
      />
      <MetricTile
        label="推送状态"
        value={data.quality.push_allowed ? "允许" : "拦截"}
        hint={data.quality.status}
        tone={data.quality.push_allowed ? "green" : "red"}
      />
    </div>
  );
}

function SymbolList({
  symbols,
  selected,
  onSelect,
}: {
  symbols: SymbolRow[];
  selected: string;
  onSelect: (symbol: string) => void;
}) {
  return (
    <div className="rounded-lg border border-[#9be7e3] bg-white/90 shadow-sm">
      <div className="flex h-12 items-center justify-between border-b border-[#c4f1ef] px-3">
        <div className="flex items-center gap-2 text-sm font-bold text-zinc-900">
          <Database className="h-4 w-4 text-[#008f8a]" />
          币种
        </div>
        <Badge tone="cyan">{symbols.length}</Badge>
      </div>
      <div className="max-h-[650px] overflow-auto p-2">
        {symbols.map((row) => (
          <button
            key={row.inst_id}
            onClick={() => onSelect(row.inst_id)}
            className={cn(
              "mb-1 grid h-14 w-full grid-cols-[1fr_auto] items-center gap-2 rounded-md border px-2 text-left transition",
              selected === row.inst_id
                ? "border-[#0abab5] bg-[#0abab5] text-white"
                : "border-transparent bg-white text-zinc-800 hover:border-[#9be7e3] hover:bg-[#f0fffe]",
            )}
          >
            <span className="min-w-0">
              <span className="block truncate text-sm font-bold">{row.base}</span>
              <span
                className={cn(
                  "block truncate text-xs",
                  selected === row.inst_id ? "text-white/80" : "text-zinc-500",
                )}
              >
                {dateTimeText(row.last_ts)}
              </span>
            </span>
            <span
              className={cn(
                "h-2.5 w-2.5 rounded-full",
                rowFresh(row) === "green" && "bg-emerald-500",
                rowFresh(row) === "amber" && "bg-amber-500",
                rowFresh(row) === "red" && "bg-rose-500",
              )}
            />
          </button>
        ))}
      </div>
    </div>
  );
}

function SignalPanel({ data }: { data: DashboardPayload }) {
  const scan = data.latest_scan;
  const signal = data.latest_signal?.signal;
  const risk = data.latest_signal?.risk;
  const qualityModel =
    data.latest_signal?.quality_model ??
    scan?.symbols?.find((item) => item.quality_model?.enabled)?.quality_model ??
    scan?.quality_model;
  const accepted = Boolean(risk?.accepted);
  const scanned = scan?.symbols_checked ?? scan?.symbols?.length ?? 0;
  const ready = scan?.ready_count ?? scan?.symbols?.filter((item) => item.would_push).length ?? 0;
  const scanAge = minutesSince(scan?.generated_at);
  const scanFresh = typeof scanAge === "number" && scanAge <= 2;
  const topReason = scan?.symbols?.find((item) => item.reason)?.reason ?? signal?.reject_reason ?? risk?.reason ?? "-";
  const badgeText = accepted ? "可推送" : scanFresh ? "已扫描" : "等待";
  return (
    <div className="rounded-lg border border-[#9be7e3] bg-white/90 p-4 shadow-sm">
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2 text-sm font-bold text-zinc-900">
            <Send className="h-4 w-4 text-[#008f8a]" />
            当前信号
          </div>
          <div className="mt-2 text-2xl font-black text-zinc-950">
            {signal?.inst_id ?? "-"}
          </div>
        </div>
        <Badge tone={accepted ? "green" : scanFresh ? "neutral" : "amber"}>{badgeText}</Badge>
      </div>

      <div className="mt-4 grid grid-cols-2 gap-3">
        <MetricTile label="方向" value={sideText(signal?.side)} />
        <MetricTile label="杠杆上限" value={`${numberText(risk?.leverage_cap, 1)}x`} />
        <MetricTile label="入场" value={numberText(signal?.entry_ref, 4)} />
        <MetricTile label="止损" value={numberText(signal?.stop_loss, 4)} />
        <MetricTile label="止盈" value={numberText(signal?.take_profit, 4)} />
        <MetricTile label="风险额" value={numberText(risk?.risk_amount, 2)} tone={accepted ? "green" : "neutral"} />
        <MetricTile label="扫描/可推" value={`${scanned}/${ready}`} tone={ready > 0 ? "green" : "neutral"} />
      </div>

      <div className="mt-3 grid grid-cols-2 gap-3">
        <MetricTile
          label="Model TP"
          value={qualityModel?.enabled ? percent(qualityModel.p_tp, 1) : "disabled"}
          hint={qualityModel?.enabled ? `support ${integerText(qualityModel.support)}` : qualityModel?.reason ?? "-"}
          tone={qualityModel?.enabled ? "green" : "neutral"}
        />
        <MetricTile
          label="Model R"
          value={qualityModel?.enabled ? numberText(qualityModel.expected_net_r, 2) : "-"}
          hint={qualityModel?.artifact_path ? "shadow only" : "-"}
        />
      </div>

      <div className="mt-4 rounded-lg border border-[#c4f1ef] bg-[#f0fffe] p-3">
        <div className="flex items-center gap-2 text-xs font-semibold text-zinc-500">
          <Clock3 className="h-4 w-4" />
          {dateTimeText(scan?.generated_at ?? signal?.ts)}
        </div>
        <div className="mt-2 text-sm font-semibold text-zinc-800">
          {scan?.error ?? topReason}
        </div>
      </div>
    </div>
  );
}

function RuntimePanel({ data }: { data: DashboardPayload }) {
  const scan = data.latest_scan;
  const ws = scan?.websocket;
  const modules = scan?.modules ?? {};
  const scanAge = minutesSince(scan?.generated_at);
  const scanFresh = typeof scanAge === "number" && scanAge <= 2;
  const wsHealthy = Boolean(ws?.running && ws?.connected && !ws?.degraded);
  const closedHealthy = modules.closed_kline_backfill?.status === "healthy";
  const signalGateHealthy = modules.signal_closed_bar_gate?.status === "healthy";
  const learningStatus = String(modules.daily_learning_review?.status ?? "-");
  const learningHealthy = ["healthy", "checking", "disabled"].includes(learningStatus);
  const tone = wsHealthy && scanFresh && closedHealthy && signalGateHealthy && learningHealthy ? "green" : "red";
  return (
    <div className="rounded-lg border border-[#9be7e3] bg-white/90 p-4 shadow-sm">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2 text-sm font-bold text-zinc-900">
          <Activity className="h-4 w-4 text-[#008f8a]" />
          运行状态
        </div>
        <Badge tone={tone}>{wsHealthy && scanFresh ? "正常" : "异常"}</Badge>
      </div>
      <div className="mt-4 grid grid-cols-2 gap-3">
        <MetricTile label="WebSocket" value={wsHealthy ? "已连接" : "未正常"} tone={wsHealthy ? "green" : "red"} />
        <MetricTile label="扫描刷新" value={ageText(scanAge)} tone={scanFresh ? "green" : "red"} />
        <MetricTile label="重连次数" value={integerText(ws?.reconnect_count)} tone={(ws?.reconnect_count ?? 0) === 0 ? "green" : "amber"} />
        <MetricTile label="检查币种" value={integerText(scan?.symbols_checked)} />
        <MetricTile label="闭合K线" value={closedHealthy ? "已补齐" : String(modules.closed_kline_backfill?.status ?? "-")} tone={closedHealthy ? "green" : "red"} />
        <MetricTile label="信号门禁" value={signalGateHealthy ? "通过" : String(modules.signal_closed_bar_gate?.status ?? "-")} tone={signalGateHealthy ? "green" : "red"} />
        <MetricTile label="学习复盘" value={learningStatus} tone={learningHealthy ? "green" : "amber"} />
      </div>
      {ws?.last_error || scan?.error ? (
        <div className="mt-4 rounded-lg border border-rose-200 bg-rose-50 p-3 text-xs font-semibold text-rose-700">
          {scan?.error ?? ws?.last_error}
        </div>
      ) : null}
    </div>
  );
}

function QualityPanel({ data }: { data: DashboardPayload }) {
  const stress = data.stress_checks;
  return (
    <div className="rounded-lg border border-[#9be7e3] bg-white/90 p-4 shadow-sm">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2 text-sm font-bold text-zinc-900">
          <ShieldCheck className="h-4 w-4 text-[#008f8a]" />
          质量门
        </div>
        <Badge tone={statusTone(data.quality.status)}>{data.quality.status}</Badge>
      </div>

      <div className="mt-4 grid gap-2 text-sm">
        <div className="flex items-center justify-between gap-3">
          <span className="text-zinc-500">最小盈亏比</span>
          <span className="font-mono font-bold">{numberText(Number(stress.min_reward_to_risk), 1)}R</span>
        </div>
        <div className="flex items-center justify-between gap-3">
          <span className="text-zinc-500">目标盈亏比</span>
          <span className="font-mono font-bold">{numberText(Number(stress.target_reward_to_risk), 1)}R</span>
        </div>
        <div className="flex items-center justify-between gap-3">
          <span className="text-zinc-500">本金最大亏损</span>
          <span className="font-mono font-bold">{percent(Number(stress.margin_loss_cap_pct), 0)}</span>
        </div>
        <div className="flex items-center justify-between gap-3">
          <span className="text-zinc-500">低分杠杆</span>
          <span className="font-mono font-bold">{numberText(Number(stress.low_score_leverage), 1)}x</span>
        </div>
        <div className="flex items-center justify-between gap-3">
          <span className="text-zinc-500">高分杠杆</span>
          <span className="font-mono font-bold">{numberText(Number(stress.high_score_leverage), 1)}x</span>
        </div>
      </div>

      <div className="mt-4 space-y-2">
        {data.quality.reasons.slice(0, 3).map((reason) => (
          <div
            key={reason}
            className="flex items-start gap-2 rounded-md border border-amber-200 bg-amber-50 p-2 text-xs font-semibold text-amber-900"
          >
            <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            <span>{reason}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function BackfillPanel({
  data,
  selected,
  selectedRow,
  candleMeta,
  timeframe,
}: {
  data: DashboardPayload;
  selected: string;
  selectedRow?: SymbolRow;
  candleMeta: CandleMeta | null;
  timeframe: string;
}) {
  const closed = data.closed_backfills?.[timeframe] ?? data.closed_backfill;
  const symbolStatus = closed?.symbols?.find((row) => row.inst_id === selected);
  const actualAge = minutesSince(candleMeta?.last_time);
  const freshTone =
    typeof actualAge === "number" && actualAge <= 90 ? "green" : rowFresh(selectedRow);
  const missing = symbolStatus?.missing_closed_bars ?? 0;

  return (
    <div className="rounded-lg border border-[#9be7e3] bg-white/90 p-4 shadow-sm">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2 text-sm font-bold text-zinc-900">
          <Activity className="h-4 w-4 text-[#008f8a]" />
          K 线补齐
        </div>
        <Badge tone={closed?.all_complete ? "green" : "amber"}>
          {closed?.all_complete ? "已补齐" : "检查中"}
        </Badge>
      </div>
      <div className="mt-4 grid grid-cols-2 gap-3">
        <MetricTile
          label="图表根数"
          value={integerText(candleMeta?.count)}
          hint={candleMeta?.source === "okx_recent" ? "OKX临时数据" : "本地数据"}
        />
        <MetricTile
          label="距最新"
          value={ageText(actualAge ?? selectedRow?.age_minutes)}
          tone={freshTone as "green" | "amber" | "red"}
        />
        <MetricTile
          label="缺闭合K"
          value={integerText(missing)}
          hint={closed?.timeframe ?? "15m"}
          tone={missing === 0 ? "green" : "amber"}
        />
        <MetricTile
          label="本轮新增"
          value={integerText(symbolStatus?.added_rows)}
          hint={symbolStatus?.status ?? "-"}
        />
      </div>
      <div className="mt-4 rounded-lg border border-[#c4f1ef] bg-[#f0fffe] p-3 text-xs font-semibold text-zinc-600">
        <div>目标闭合：{dateTimeText(symbolStatus?.expected_latest_closed ?? closed?.expected_latest_closed)}</div>
        <div className="mt-1">实际末根：{dateTimeText(candleMeta?.last_time ?? selectedRow?.last_ts)}</div>
        <div className="mt-1">下次补齐：{dateTimeText(closed?.next_run_at)}</div>
        {symbolStatus?.error ? <div className="mt-2 text-rose-700">{symbolStatus.error}</div> : null}
      </div>
    </div>
  );
}

function LearningPanel({ data }: { data: DashboardPayload }) {
  const review = data.learning_review;
  const shadow = review?.shadow_summary ?? {};
  const meta = review?.train_grid_meta ?? {};
  const reasons = review?.reasons ?? [];
  const candidatePf = review?.candidate_valid_summary?.profit_factor;
  const currentPf = review?.current_valid_summary?.profit_factor;

  return (
    <div className="rounded-lg border border-[#9be7e3] bg-white/90 p-4 shadow-sm">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2 text-sm font-bold text-zinc-900">
          <ShieldCheck className="h-4 w-4 text-[#008f8a]" />
          学习闭环
        </div>
        <Badge tone={review?.candidate_gate_passed ? "green" : "amber"}>
          {review?.candidate_gate_passed ? "候选过门" : "审查中"}
        </Badge>
      </div>

      <div className="mt-4 grid grid-cols-2 gap-3">
        <MetricTile
          label="候选 PF"
          value={numberText(candidatePf, 3)}
          hint={`当前 ${numberText(currentPf, 3)}`}
          tone={review?.candidate_gate_passed ? "green" : "amber"}
        />
        <MetricTile
          label="候选组数"
          value={integerText(Number(meta.candidate_count ?? 0))}
          hint={String(meta.selected_params_source ?? "-")}
        />
        <MetricTile
          label="影子闭合"
          value={integerText(Number(shadow.closed ?? 0))}
          hint={`质量 ${numberText(Number(shadow.avg_quality_score ?? 0), 1)}`}
        />
        <MetricTile
          label="自动替换"
          value={review?.auto_promote_enabled ? "开启" : "关闭"}
          hint={review?.promotion_allowed ? "允许" : "只出候选"}
          tone={review?.promotion_allowed ? "green" : "neutral"}
        />
      </div>

      <div className="mt-4 rounded-lg border border-[#c4f1ef] bg-[#f0fffe] p-3 text-xs font-semibold text-zinc-600">
        <div>最近审查：{dateTimeText(review?.generated_at)}</div>
        <div className="mt-1">下次审查：{dateTimeText(review?.next_run_at)}</div>
        {reasons.slice(0, 3).map((reason) => (
          <div key={reason} className="mt-2 text-amber-800">
            {reason}
          </div>
        ))}
      </div>
    </div>
  );
}

export function Dashboard() {
  const [data, setData] = useState<DashboardPayload>(emptyData);
  const [selected, setSelected] = useState("BTC-USDT-SWAP");
  const [timeframe, setTimeframe] = useState<(typeof timeframes)[number]>("15m");
  const [rangeDays, setRangeDays] = useState(30);
  const [candles, setCandles] = useState<Candle[]>([]);
  const [candleMeta, setCandleMeta] = useState<CandleMeta | null>(null);
  const [busy, setBusy] = useState(true);
  const [chartError, setChartError] = useState("");
  const [lastRefresh, setLastRefresh] = useState("");

  const selectedRow = useMemo(
    () => data.symbols.find((row) => row.inst_id === selected),
    [data.symbols, selected],
  );
  const candleLimit = useMemo(() => limitFor(rangeDays, timeframe), [rangeDays, timeframe]);

  const loadDashboard = useCallback(async () => {
    const response = await fetch("/api/dashboard", { cache: "no-store" });
    const payload = (await response.json()) as DashboardPayload;
    setData(payload);
    setSelected((current) =>
      payload.symbols.some((row) => row.inst_id === current)
        ? current
        : payload.symbols[0]?.inst_id ?? "BTC-USDT-SWAP",
    );
    setLastRefresh(new Date().toLocaleTimeString("zh-CN", { hour12: false }));
  }, []);

  const loadCandles = useCallback(async (symbol: string, tf: string, limit: number) => {
    setChartError("");
    const response = await fetch(
      `/api/candles/${encodeURIComponent(symbol)}?timeframe=${tf}&limit=${limit}`,
      { cache: "no-store" },
    );
    const payload = (await response.json()) as CandlePayload;
    if (!response.ok || payload.error) {
      setChartError(payload.error ?? "candles_error");
      setCandles([]);
      setCandleMeta(null);
      return;
    }
    setCandles(payload.candles);
    setCandleMeta({
      symbol: payload.symbol,
      timeframe: payload.timeframe ?? tf,
      source: payload.source ?? "local",
      count: payload.count,
      last_time: payload.last_time,
    });
  }, []);

  const refreshAll = useCallback(async () => {
    setBusy(true);
    try {
      await loadDashboard();
      await loadCandles(selected, timeframe, candleLimit);
    } finally {
      setBusy(false);
    }
  }, [candleLimit, loadCandles, loadDashboard, selected, timeframe]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void refreshAll();
    }, 0);
    const interval = window.setInterval(() => {
      void refreshAll();
    }, 30000);
    return () => {
      window.clearTimeout(timer);
      window.clearInterval(interval);
    };
  }, [refreshAll]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void loadCandles(selected, timeframe, candleLimit);
    }, 0);
    const interval = window.setInterval(() => {
      void loadCandles(selected, timeframe, candleLimit);
    }, 10000);
    return () => {
      window.clearTimeout(timer);
      window.clearInterval(interval);
    };
  }, [candleLimit, loadCandles, selected, timeframe]);

  return (
    <main className="min-h-screen bg-[#e6fbfa] text-zinc-950">
      <div className="mx-auto flex w-full max-w-[1760px] flex-col gap-4 p-4 lg:p-6">
        <header className="flex flex-col gap-3 border-b border-[#9be7e3] pb-4 lg:flex-row lg:items-center lg:justify-between">
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <h1 className="text-2xl font-black tracking-normal text-zinc-950">
                OKX Signal Desk
              </h1>
              <Badge tone="cyan">{data.signal_timeframe}</Badge>
              <Badge tone="neutral">{data.trend_timeframe}</Badge>
              <Badge tone={data.quality.push_allowed ? "green" : "red"}>
                {data.quality.push_allowed ? "Push OK" : "Push Blocked"}
              </Badge>
            </div>
            <div className="mt-2 flex flex-wrap items-center gap-3 text-sm font-semibold text-zinc-600">
              <span>{data.dataset}</span>
              <span>{data.symbols.length} symbols</span>
              <span>{lastRefresh || "-"}</span>
            </div>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button onClick={refreshAll} disabled={busy}>
              <RefreshCw className={cn("h-4 w-4", busy && "animate-spin")} />
              刷新
            </Button>
          </div>
        </header>

        <MetricGrid data={data} />

        <section className="grid gap-4 xl:grid-cols-[240px_minmax(0,1fr)_360px]">
          <SymbolList symbols={data.symbols} selected={selected} onSelect={setSelected} />

          <div className="min-w-0 self-start rounded-lg border border-[#9be7e3] bg-white/90 p-3 shadow-sm">
            <div className="mb-3 flex flex-col gap-3 xl:flex-row xl:items-center xl:justify-between">
              <div>
                <div className="flex items-center gap-2 text-lg font-black text-zinc-950">
                  <Target className="h-5 w-5 text-[#008f8a]" />
                  {selected}
                </div>
                <div className="mt-1 text-xs font-semibold text-zinc-500">
                  {candles.length} candles / {dateTimeText(candleMeta?.last_time ?? selectedRow?.last_ts)}
                  {candleMeta?.source === "okx_recent" ? " / OKX临时" : " / 本地"}
                </div>
              </div>
              <div className="flex flex-wrap gap-2">
                {timeframes.map((item) => (
                  <Button key={item} active={timeframe === item} onClick={() => setTimeframe(item)}>
                    <LineChart className="h-4 w-4" />
                    {item}
                  </Button>
                ))}
                {ranges.map((item) => (
                  <Button key={item.days} active={rangeDays === item.days} onClick={() => setRangeDays(item.days)}>
                    <CalendarRange className="h-4 w-4" />
                    {item.label}
                  </Button>
                ))}
                <Badge tone={rowFresh(selectedRow)}>{selectedRow?.status ?? "unknown"}</Badge>
              </div>
            </div>

            {chartError ? (
              <div className="flex h-[430px] items-center justify-center rounded-lg border border-rose-200 bg-rose-50 px-4 text-center text-sm font-bold text-rose-700">
                {chartError}
              </div>
            ) : candles.length ? (
              <KlineChart candles={candles} symbol={selected} signal={data.latest_signal} />
            ) : (
              <div className="h-[430px] rounded-lg bg-zinc-950" />
            )}
          </div>

          <aside className="grid gap-4">
            <RuntimePanel data={data} />
            <SignalPanel data={data} />
            <QualityPanel data={data} />
            <BackfillPanel
              data={data}
              selected={selected}
              selectedRow={selectedRow}
              candleMeta={candleMeta}
              timeframe={timeframe}
            />
            <LearningPanel data={data} />
          </aside>
        </section>
      </div>
    </main>
  );
}
