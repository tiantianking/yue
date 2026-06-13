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
};

export type ClosedBackfillStatus = {
  generated_at?: string;
  timeframe?: string;
  dataset?: string;
  expected_latest_closed?: string;
  next_run_at?: string;
  all_complete?: boolean;
  symbols_checked?: number;
  symbols?: Array<{
    inst_id: string;
    status: string;
    last_ts?: string;
    expected_latest_closed?: string;
    missing_closed_bars?: number;
    added_rows?: number;
    error?: string;
  }>;
};

export type DashboardPayload = {
  generated_at: string;
  project_root: string;
  signal_timeframe: string;
  trend_timeframe: string;
  dataset: string;
  symbols: SymbolRow[];
  quality: {
    status: string;
    push_allowed: boolean;
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
  closed_backfill: ClosedBackfillStatus | null;
};

export type Candle = {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume?: number;
};
