export type JsonRecord = Record<string, unknown>;

export type SummaryMetrics = {
  total_return?: number;
  profit_factor?: number;
  payoff_ratio?: number;
  win_rate?: number;
  total_trades?: number;
  max_drawdown?: number;
  avg_hold_hours?: number;
  max_loss_streak?: number;
  status?: string;
};

export type StrategyParams = {
  fast_ema?: number;
  slow_ema?: number;
  breakout_window?: number;
  atr_stop_mult?: number;
  take_profit_mult?: number;
  max_hold_bars?: number;
  atr_window?: number;
};

export type QualityModelShadow = {
  enabled?: boolean;
  artifact_path?: string;
  reason?: string | null;
  p_tp?: number;
  p_sl?: number;
  p_timeout?: number;
  expected_net_r?: number;
  uncertainty?: number;
  rank_score?: number;
  support?: number;
  feature_columns?: string[];
};

export type LatestSignal = {
  signal?: {
    ts?: string;
    inst_id?: string;
    side?: "long" | "short" | "flat" | string;
    entry_ref?: number | null;
    stop_loss?: number | null;
    take_profit?: number | null;
    max_hold_bars?: number | null;
    reason_codes?: string[];
    reject_reason?: string;
    risk_reward_ratio?: number | null;
  };
  risk?: {
    accepted?: boolean;
    reason?: string;
    leverage_cap?: number;
    qty?: number | null;
    risk_amount?: number | null;
    margin_mode?: string;
    position_mode?: string;
  };
  live_order_enabled?: boolean;
  mode?: string;
  quality_model?: QualityModelShadow;
};

export type LatestScanStatus = {
  generated_at?: string;
  status?: string;
  runtime_status?: "online" | "stale" | "offline" | "error" | string;
  runtime_reason?: string;
  age_minutes?: number | null;
  error?: string | null;
  dataset?: string;
  signal_timeframe?: string;
  trend_timeframe?: string;
  push_allowed?: boolean;
  manifest_status?: {
    ok?: boolean;
    push_allowed?: boolean;
    reason?: string;
    selected_params?: StrategyParams;
    manifest_hash?: string;
    research_run_id?: string;
    approved_at?: string;
    [key: string]: unknown;
  } | null;
  selected_params?: StrategyParams;
  symbols_checked?: number;
  ready_count?: number;
  websocket?: {
    running?: boolean;
    connected?: boolean;
    degraded?: boolean;
    reconnect_count?: number;
    last_error?: string | null;
    last_open_at?: number | null;
    last_message_at?: number | null;
    last_close?: { code?: number | null; message?: string | null } | null;
    url?: string;
    proxy?: string | null;
    last_message_age_minutes?: number | null;
  } | null;
  modules?: Record<
    string,
    {
      status?: string;
      updated_at?: string;
      [key: string]: unknown;
    }
  >;
  symbols?: Array<{
    symbol?: string;
    reason?: string;
    risk_reason?: string | null;
    would_push?: boolean;
    side?: string | null;
    kline_time?: string | null;
    close?: number | null;
    bias?: string | null;
    regime?: string | null;
    raw_score?: number | null;
    final_score?: number | null;
    shadow_adjustment?: number | null;
    quality_model?: QualityModelShadow | null;
    breakout_gap_pct?: number | null;
  }>;
  quality_model?: QualityModelShadow | null;
  lifecycle_summary?: {
    total?: number;
    active?: number;
    terminal?: number;
    outbox?: {
      pending?: number;
      sent?: number;
      failed?: number;
      in_progress?: number;
      dead_letter?: number;
      updated_at?: string | null;
    };
    [key: string]: unknown;
  } | null;
  last_signal?: LatestSignal | null;
};

export type BackfillRow = {
  inst_id: string;
  rows_before?: number;
  rows_after?: number;
  added_rows?: number;
  first_ts?: string;
  last_ts?: string;
  requests?: number;
  status?: string;
  error?: string;
};

export type SymbolRow = {
  inst_id: string;
  base: string;
  status: string;
  rows_after: number;
  added_rows: number;
  first_ts: string;
  last_ts: string;
  age_minutes: number | null;
  error: string;
  source?: string;
};

export type ClosedBackfillStatus = {
  generated_at?: string;
  timeframe?: string;
  dataset?: string;
  expected_latest_closed?: string;
  next_run_at?: string;
  all_complete?: boolean;
  symbols_checked?: number;
  write_failures?: number;
  symbols?: Array<{
    inst_id: string;
    status: string;
    rows_before?: number;
    rows_after?: number;
    first_ts?: string;
    last_ts?: string;
    expected_latest_closed?: string;
    missing_closed_bars?: number;
    added_rows?: number;
    error?: string;
    data_complete?: boolean;
    write_attempted?: boolean;
    write_succeeded?: boolean;
    write_error?: string;
  }>;
};

export type DailyLearningReviewStatus = {
  status?: string;
  generated_at?: string;
  next_run_at?: string;
  candidate_gate_passed?: boolean;
  auto_promote_enabled?: boolean;
  promotion_allowed?: boolean;
  reasons?: string[];
  train_grid_meta?: Record<string, unknown>;
  shadow_summary?: Record<string, unknown>;
  overfit_checks?: Record<string, unknown>;
  current_valid_summary?: SummaryMetrics;
  candidate_valid_summary?: SummaryMetrics;
  candidate_params?: StrategyParams;
};

export type DashboardPayload = {
  generated_at: string;
  runtime_mode: "formal_push" | "research_observation";
  runtime_status_source: string;
  latest_scan_age_seconds: number | null;
  backfill_status_age_seconds: number | null;
  project_root: string;
  signal_timeframe: string;
  trend_timeframe: string;
  dataset: string;
  symbols: SymbolRow[];
  quality: {
    status: string;
    push_allowed: boolean;
    manifest_reason: string;
    params_source: string;
    reasons: string[];
    push_blocking_reasons: string[];
    stale_symbols: string[];
  };
  train_summary: SummaryMetrics;
  valid_summary: SummaryMetrics;
  stress_checks: Record<string, unknown>;
  selected_params: StrategyParams;
  risk_config: Record<string, unknown>;
  latest_signal: LatestSignal | null;
  latest_scan?: LatestScanStatus | null;
  closed_backfill: ClosedBackfillStatus | null;
  closed_backfill_5m?: ClosedBackfillStatus | null;
  closed_backfills?: Record<string, ClosedBackfillStatus | null>;
  learning_review: DailyLearningReviewStatus | null;
};

export type Candle = {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume?: number;
};
