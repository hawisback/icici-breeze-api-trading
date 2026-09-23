/**
 * API client communicating with FastAPI API Gateway (/api/v1).
 */

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api/v1";

export interface SystemHealth {
  status: string;
  timestamp: string;
  services: {
    api_gateway: string;
    broker_session: string;
    market_feed: string;
    order_feed: string;
    risk_engine: string;
    oms: string;
    portfolio: string;
    system_mode: string;
  };
  config?: {
    broker_backend?: "breeze" | "kite";
    [key: string]: unknown;
  };
}

export interface QuoteData {
  instrument_id: string;
  symbol: string;
  last_price: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  change_pct: number;
  timestamp: string;
}

export interface CandleData {
  time: number; // Unix timestamp in seconds
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  source?: string;
  isoTime?: string;
}

export interface OptionStrikeData {
  strike: number;
  call: {
    instrument_id: string;
    symbol: string;
    ltp: number;
    change_pct: number;
    volume: number;
    open_interest: number;
    bid: number;
    ask: number;
    lot_size: number;
  } | null;
  put: {
    instrument_id: string;
    symbol: string;
    ltp: number;
    change_pct: number;
    volume: number;
    open_interest: number;
    bid: number;
    ask: number;
    lot_size: number;
  } | null;
}

export interface OptionChainResponse {
  underlying: string;
  spot_price: number;
  expiry: string;
  available_expiries: string[];
  atm_strike?: number;
  source?: string;
  strikes: OptionStrikeData[];
}

export interface OrderData {
  order_id: string;
  intent_id: string;
  client_order_id: string;
  broker_order_id: string | null;
  instrument_id: string;
  symbol: string;
  side: "BUY" | "SELL";
  order_type: "LIMIT" | "MARKET";
  quantity: number;
  filled_quantity: number;
  remaining_quantity: number;
  price: number;
  average_price: number;
  status: string;
  status_message: string | null;
  trading_mode: "PAPER" | "SHADOW" | "LIVE";
  created_at: string;
  updated_at: string;
}

export interface PositionData {
  position_id: string;
  instrument_id: string;
  symbol: string;
  quantity: number;
  buy_quantity: number;
  sell_quantity: number;
  average_price: number;
  current_price: number;
  realized_pnl: number;
  unrealized_pnl: number;
  total_pnl: number;
  trading_mode: string;
  updated_at: string;
}

export interface PnLSummary {
  realized_pnl: number;
  unrealized_pnl: number;
  total_pnl: number;
  day_pnl: number;
  open_positions_count: number;
  timestamp: string;
}

export interface AuditLogData {
  event_id: string;
  event_type: string;
  correlation_id: string;
  source: string;
  payload: any;
  occurred_at: string;
}

// Fetchers
export async function fetchSystemHealth(): Promise<SystemHealth> {
  const res = await fetch(`${API_BASE}/system/health`);
  if (!res.ok) throw new Error("Failed to fetch system health");
  return res.json();
}

export async function fetchQuotes(): Promise<QuoteData[]> {
  const res = await fetch(`${API_BASE}/market/quotes`);
  if (!res.ok) throw new Error("Failed to fetch quotes");
  return res.json();
}

export async function fetchCandles(instrumentId: string, interval: string = "5m"): Promise<CandleData[]> {
  const res = await fetch(`${API_BASE}/market/candles?instrument_id=${instrumentId}&interval=${interval}&limit=120`);
  if (!res.ok) throw new Error("Failed to fetch candles");
  const data = await res.json();
  const byTime = new Map<number, CandleData>();
  for (const c of data) {
    const time = Math.floor(new Date(c.start_time).getTime() / 1000);
    if (!Number.isFinite(time)) continue;
    byTime.set(time, {
      time,
      open: c.open,
      high: c.high,
      low: c.low,
      close: c.close,
      volume: c.volume,
      source: c.source || "BREEZE",
      isoTime: c.start_time,
    });
  }
  return Array.from(byTime.values()).sort((a, b) => a.time - b.time);
}

export async function fetchOptionChain(underlying: string = "NIFTY", expiry?: string): Promise<OptionChainResponse> {
  let url = `${API_BASE}/options/chain?underlying=${underlying}`;
  if (expiry) url += `&expiry=${expiry}`;
  const res = await fetch(url);
  if (!res.ok) throw new Error("Failed to fetch option chain");
  return res.json();
}

export async function fetchOrders(): Promise<OrderData[]> {
  const res = await fetch(`${API_BASE}/orders`);
  if (!res.ok) throw new Error("Failed to fetch orders");
  return res.json();
}

export async function createOrder(order: {
  instrument_id: string;
  symbol: string;
  side: "BUY" | "SELL";
  order_type: "LIMIT" | "MARKET";
  quantity: number;
  price: number;
  trading_mode: "PAPER" | "SHADOW" | "LIVE";
}): Promise<OrderData> {
  const res = await fetch(`${API_BASE}/orders`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(order),
  });
  if (!res.ok) {
    const err = await res.json();
    throw new Error(err.detail || "Failed to create order");
  }
  return res.json();
}

export async function fetchPositions(): Promise<PositionData[]> {
  const res = await fetch(`${API_BASE}/positions`);
  if (!res.ok) throw new Error("Failed to fetch positions");
  return res.json();
}

export async function fetchPnLSummary(): Promise<PnLSummary> {
  const res = await fetch(`${API_BASE}/pnl/summary`);
  if (!res.ok) throw new Error("Failed to fetch PnL summary");
  return res.json();
}

export async function fetchAuditLogs(): Promise<AuditLogData[]> {
  const res = await fetch(`${API_BASE}/audit/logs?limit=50`);
  if (!res.ok) throw new Error("Failed to fetch audit logs");
  return res.json();
}

export async function triggerKillSwitch(action: string, reason: string): Promise<any> {
  const res = await fetch(`${API_BASE}/risk/kill-switch`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action, reason }),
  });
  if (!res.ok) throw new Error("Failed to trigger kill switch");
  return res.json();
}

export async function setSystemMode(mode: string): Promise<any> {
  const res = await fetch(`${API_BASE}/risk/system-mode`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ mode }),
  });
  if (!res.ok) throw new Error("Failed to set system mode");
  return res.json();
}

export async function fetchLoginUrl(): Promise<{ login_url: string; api_key: string; broker?: "breeze" | "kite" }> {
  const res = await fetch(`${API_BASE}/broker/session/login-url`);
  if (!res.ok) throw new Error("Failed to fetch login URL");
  return res.json();
}

// ==============================================================================
// Auto-Trading Strategy API
// ==============================================================================

export interface StrategyFleetStatusData {
  enabled: boolean;
  label?: string;
  state: string;
  execution_mode?: string;
  live_trading_allowed?: boolean;
  candidate_id?: string | null;
  candidate_spec_fingerprint?: string | null;
  paper_open_trades?: number;
  paper_closed_trades?: number;
  paper_net_pnl?: number;
  current_r?: number | null;
  current_trailing_stop?: number | null;
  active_trade_id?: string | null;
}

export interface CandidatePaperStatusData {
  status: string;
  candidate_id?: string;
  candidate_spec_fingerprint?: string;
  execution_mode?: string;
  live_trading_allowed?: boolean;
  broker_called?: boolean;
  orders_created?: boolean;
  paper_open_trades?: number;
  paper_closed_trades?: number;
  paper_incomplete_trades?: number;
  paper_net_pnl?: number;
  active_candidate_trade?: Record<string, any> | null;
  active_paper_trade?: Record<string, any> | null;
  latest_signal?: Record<string, any> | null;
  market?: Record<string, any> | null;
  [key: string]: any;
}

export interface StrategyStatusData {
  scheduler?: {
    running: boolean;
    task_done?: boolean | null;
    cycle_count: number;
    last_cycle_status?: string | null;
    last_evaluation_time?: string | null;
    last_evaluation_age_seconds?: number | null;
    evaluation_interval_seconds: number;
  };
  market_data?: {
    provider: string;
    provider_active: boolean;
    futures_instrument?: string | null;
    futures_candle_count: number;
    latest_futures_candle?: string | null;
    last_error?: string | null;
    last_evaluation_time?: string | null;
  };
  config: {
    mode: "PAPER" | "SHADOW_ONLY" | "LIVE" | "DISABLED";
    auto_trade_enabled: boolean;
    system_armed: boolean;
    kill_switch: boolean;
    option_selection: {
      max_option_premium: number;
      min_option_premium: number;
      max_otm_strikes: number;
      min_open_interest: number;
      max_bid_ask_spread_pct: number;
      prefer_premium_closest_to_cap: boolean;
      use_current_expiry_on_0dte: boolean;
      preferred_delta_min: number;
      preferred_delta_max: number;
      allowed_delta_min: number;
      allowed_delta_max: number;
      minimum_expiry_sessions_remaining: number;
      max_quote_age_seconds: number;
      minimum_volume: number;
    };
    risk: {
      max_lots_per_trade: number;
      entry_order_timeout_sec: number;
      max_trades_per_strategy_per_day: number;
      breakeven_buffer_points: number;
      max_trade_capital: number;
      risk_per_trade_pct_of_account: number;
      max_daily_loss_r: number;
      max_daily_loss_pct: number;
      max_failed_trades_per_strategy: number;
      max_trades_per_day: number;
      max_concurrent_positions: number;
      cooldown_after_loss_min: number;
      option_hard_stop_pct: number;
      account_equity: number;
    };
    session: {
      strategy_b_no_new_trade_before: string;
      no_new_trade_before: string;
      no_new_trade_after: string;
      force_exit_time: string;
    };
    tunables: {
      strat_b_min_confirmation: number;
      box_max_height_atr: number;
      bb_width_percentile_threshold: number;
      compression_lookback_bars: number;
      box_max_age_bars: number;
      breakout_buffer_atr: number;
      breakout_max_extension_atr: number;
      evaluation_interval_sec: number;
      trend_pullback_enabled: boolean;
      volatility_breakout_enabled: boolean;
      ema_fast_period: number;
      ema_slow_period: number;
      adx_period: number;
      adx_threshold: number;
      momentum_adx_min_delta_2bars: number;
      momentum_ema20_slope_min_atr: number;
      momentum_ema20_slope_max_atr: number;
      atr_period: number;
      ema_separation_min_atr: number;
      confluence_distance_atr: number;
      sr_zone_atr: number;
      confirmation_min_body_ratio: number;
      confirmation_close_location_pct: number;
      confirmation_max_range_atr: number;
      trigger_buffer_atr: number;
      trigger_validity_bars: number;
      maximum_chase_atr: number;
      structural_stop_buffer_atr: number;
      minimum_stop_distance_atr: number;
      maximum_stop_distance_atr: number;
      minimum_room_to_opposing_sr_r: number;
      t1_r: number;
      runner_target_reference_r: number;
      trailing_activation_r: number;
      entry_session_start: string;
      entry_session_end: string;
      forced_exit_time: string;
      rvol_threshold: number;
      ema_slope_threshold: number;
      min_confirmation_score?: number;
      supertrend_period: number;
      supertrend_multiplier: number;
    };
    strategy_a_revision: number;
  };
  features: {
    timestamp: string;
    spot_price: number;
    ema9_15m: number;
    ema20_15m: number;
    ema50_15m: number;
    ema20_slope_15m: number;
    adx_15m: number;
    plus_di_15m: number;
    minus_di_15m: number;
    ema9_5m: number;
    ema20_5m: number;
    rsi_5m: number;
    atr_5m: number;
    daily_atr: number;
    supertrend_direction: string;
    bb_width_percentile: number;
    futures_price: number;
    futures_vwap: number;
    rvol_5m: number;
    futures_buildup: string;
    bull_derivatives_score: number;
    bear_derivatives_score: number;
    expected_daily_points: number;
    remaining_session_points: number;
    trend_regime: string;
  };
  active_trades: AutoTradeData[];
  signals: any[];
  strategy_c_shadow?: CandidatePaperStatusData;
  strategy_c_paper?: CandidatePaperStatusData;
  strategy_d_paper?: CandidatePaperStatusData;
  strategies: {
    trend_pullback: StrategyFleetStatusData;
    volatility_breakout: StrategyFleetStatusData;
    di_continuation: StrategyFleetStatusData;
    sr_momentum_breakout: StrategyFleetStatusData;
  };
  trigger_diagnostics?: TriggerDiagnosticsResponseData;
  active_overrides?: ThresholdOverridesData;
  system_time: string;
  in_trading_window: boolean;
}

export interface TriggerConditionData {
  id: string;
  name: string;
  current_value: string;
  target_threshold: string;
  unit?: string;
  status: "PASSED" | "PENDING" | "BLOCKED" | "N/A";
  gap_description: string;
}

export interface StrategyTriggerDiagnosticsData {
  strategy: string;
  strategy_label: string;
  direction: "BULLISH" | "BEARISH";
  option_type: "CALL" | "PUT";
  overall_status: "READY_TO_TRIGGER" | "WAITING" | "BLOCKED" | "PAPER_OPEN";
  phase_state?: string;
  phase_summary?: {
    regime?: {
      status: string;
      direction_score: string;
      adx: number;
      qualified?: boolean;
      qualified_since?: string | null;
      setup_cutoff?: string | null;
      cutoff_event?: string;
    };
    impulse?: {
      found: boolean;
      height_atr: number;
      source?: string;
      impulse_start?: string | null;
      impulse_end?: string | null;
      impulse_direction?: string;
      impulse_points?: number;
      impulse_atr_multiple?: number;
      impulse_crossed_setup_cutoff?: boolean;
    };
    impulse_search?: {
      setup_cutoff_timestamp?: string | null;
      search_start_timestamp?: string | null;
      included_pre_cutoff?: boolean;
      pre_cutoff_candles_included?: number;
      post_cutoff_candles_searched?: number;
      search_candle_count?: number;
      max_pre_cutoff_candles?: number;
      rejection_reasons?: { category: string; detail: string }[];
    };
    impulse_rejection_reason?: string | null;
    setup_direction?: string;
    macro_regime_qualified?: boolean;
    regime_first_qualified_at?: string | null;
    setup_cutoff?: string | null;
    setup_cutoff_event?: string;
    setup_age_seconds?: number | null;
    completed_5m_candles_since_cutoff?: number;
    pullback?: { state: string; bars: number; depth_pct: number; retest: string; depth_range?: string; max_depth_inclusive?: boolean };
    compression?: { bb_percentile: number; is_compressed: boolean };
    box?: { status: string; high: number; low: number; height_pts: number; height_atr: number; bars_active: number };
    trigger?: { waiting_for: string; gap_pts: number };
    extension?: { status: string; extension_atr: number; max_allowed_atr: number };
    confirmation?: { score: number; required: number; passed_factors?: string[] };
    risk?: { initial_r_atr: number; stop: number };
    active_evaluator_version?: string;
    strategy_a_v2?: {
      compatibility_envelope?: boolean;
      active_evaluator_version?: string;
      data: { contract: string; completed_candle_timestamp: string; close: number };
      trend: {
        passed: boolean;
        reason: string;
        ema20: number;
        ema50: number;
        adx: number;
        plus_di: number;
        minus_di: number;
        adx_delta_2bars?: number | null;
        ema20_directional_slope_atr?: number | null;
        components?: {
          ema_order: boolean;
          di_direction: boolean;
          ema_separation: boolean;
          momentum_context: boolean;
          adx_decay: boolean;
          ema20_slope: boolean;
        };
      };
      confluence: { passed: boolean; reason: string; references: string[]; level?: number | null; support?: number | null; resistance?: number | null; vwap: number };
      confirmation: { passed: boolean; reason: string; body_ratio: number; range_atr: number };
      trigger: { state: string; trigger_price?: number | null; distance_pts?: number | null };
      risk: { passed?: boolean | null; reason?: string; structural_stop?: number | null; initial_r_points?: number | null };
    };
  };
  passed_count: number;
  total_count: number;
  ready_pct: number;
  key_blocker: string;
  target_entry_level?: number | null;
  current_spot: number;
  distance_pts?: number | null;
  conditions: TriggerConditionData[];
}

export interface GateBlockersData {
  kill_switch_active: boolean;
  auto_trade_enabled: boolean;
  within_trading_window: boolean;
  max_positions_reached: boolean;
  in_cooldown: boolean;
  daily_trades_count: number;
  daily_trades_max: number;
  system_armed: boolean;
  primary_blocker?: string | null;
}

export interface ThresholdOverridesData {
  max_option_premium_cap?: number | null;
  min_option_premium_floor?: number | null;
  adx_threshold?: number | null;
  rvol_threshold?: number | null;
  ema_slope_threshold?: number | null;
  min_confirmation_score?: number | null;
  strat_b_min_confirmation?: number | null;
  box_max_height_atr?: number | null;
  bull_derivatives_score?: number | null;
  bear_derivatives_score?: number | null;
  bb_width_percentile?: number | null;
  bypass_entry_window: boolean;
}

export interface TriggerDiagnosticsResponseData {
  system_time: string;
  gates: GateBlockersData;
  strategies: StrategyTriggerDiagnosticsData[];
  active_overrides: ThresholdOverridesData;
}

export interface AutoTradeData {
  trade_id: string;
  mode: string;
  strategy: string;
  direction: "BULLISH" | "BEARISH";
  option_type: "CALL" | "PUT";
  contract_symbol: string;
  contract_instrument_id: string;
  expiry: string;
  strike: number;
  quantity: number;
  lot_size: number;
  lots: number;
  entry_time: string;
  entry_option_price: number;
  entry_spot_price: number;
  initial_structural_stop: number;
  initial_r_points: number;
  box_high?: number | null;
  box_low?: number | null;
  atr_at_lock?: number | null;
  current_option_price: number;
  current_spot_price: number;
  current_trailing_stop: number;
  option_hard_stop_price: number;
  current_r: number;
  peak_r: number;
  mfe_points: number;
  mae_points: number;
  reversal_score: number;
  state: "ENTRY_PENDING" | "EXIT_PENDING" | "OPEN_INITIAL_RISK" | "PROTECTED_BREAKEVEN" | "PROFIT_LOCKED" | "RUNNER_MODE" | "CLOSED";
  unrealized_pnl: number;
  exit_time?: string | null;
  exit_option_price?: number | null;
  exit_spot_price?: number | null;
  exit_reason?: string | null;
  gross_pnl?: number | null;
  net_pnl?: number | null;
  realized_r?: number | null;
  signal_id?: string | null;
  selected_contract_snapshot?: Record<string, any>;
  current_bid?: number | null;
  current_ask?: number | null;
  current_ltp?: number | null;
  current_quote_source?: string | null;
  current_quote_freshness_seconds?: number | null;
  pending_exit_reason?: string | null;
  option_data_status?: string;
  option_data_quality_reasons?: string[];
  futures_contract_id?: string | null;
  underlying_entry_price?: number | null;
  underlying_current_price?: number | null;
  underlying_exit_price?: number | null;
  underlying_structural_stop?: number | null;
  underlying_r?: number | null;
  underlying_exit_reason?: string | null;
  underlying_exit_time?: string | null;
  underlying_outcome_status?: string | null;
  option_exit_reason?: string | null;
  option_exit_time?: string | null;
  initial_quantity?: number | null;
  remaining_quantity?: number | null;
  t1_reached?: boolean;
  t1_exit_quantity?: number;
  t1_exit_pending?: boolean;
  t1_decision_underlying_price?: number | null;
  t1_decision_r?: number | null;
  t1_realized_r?: number | null;
  runner_realized_r?: number | null;
  partial_exit_filled_quantity?: number;
  partial_exit_price?: number | null;
  partial_exit_reason?: string | null;
  selected_option_delta?: number | null;
  selected_option_delta_source?: string;
  selected_option_gamma?: number | null;
  selected_option_gamma_source?: string;
  risk_budget?: number | null;
  estimated_option_loss_at_structural_stop?: number | null;
  return_on_premium_pct?: number | null;
  slippage_cost?: number | null;
  transaction_costs?: number | null;
}

export interface DecisionLogData {
  id: string;
  timestamp: string;
  category: string;
  strategy?: string | null;
  message: string;
  details: Record<string, any>;
}

export async function fetchStrategyStatus(): Promise<StrategyStatusData> {
  const res = await fetch(`${API_BASE}/strategies/status`);
  if (!res.ok) throw new Error("Failed to fetch strategy status");
  return res.json();
}

export async function fetchStrategyConfig(): Promise<any> {
  const res = await fetch(`${API_BASE}/strategies/config`);
  if (!res.ok) throw new Error("Failed to fetch strategy config");
  return res.json();
}

export async function updateStrategyConfig(config: any): Promise<any> {
  const res = await fetch(`${API_BASE}/strategies/config`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(config),
  });
  if (!res.ok) {
    const error = await res.json().catch(() => ({}));
    const detail = Array.isArray(error.detail)
      ? error.detail.map((item: any) => item.msg || item).join(", ")
      : error.detail;
    throw new Error(detail || "Failed to update strategy config");
  }
  return res.json();
}

export async function armStrategySystem(armed: boolean): Promise<any> {
  const res = await fetch(`${API_BASE}/strategies/arm`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ armed }),
  });
  if (!res.ok) throw new Error("Failed to arm system");
  return res.json();
}

export async function setStrategyAutoTrade(enabled: boolean): Promise<any> {
  const res = await fetch(`${API_BASE}/strategies/auto-trade`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ enabled }),
  });
  if (!res.ok) throw new Error("Failed to set auto-trade");
  return res.json();
}

export async function toggleStrategyKillSwitch(active: boolean): Promise<any> {
  const res = await fetch(`${API_BASE}/strategies/kill-switch`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ active }),
  });
  if (!res.ok) throw new Error("Failed to toggle kill switch");
  return res.json();
}

export async function evaluateStrategyNow(): Promise<any> {
  const res = await fetch(`${API_BASE}/strategies/evaluate-now`, {
    method: "POST",
  });
  if (!res.ok) throw new Error("Failed to evaluate strategy");
  return res.json();
}

export async function fetchStrategyDecisionLog(limit: number = 100): Promise<DecisionLogData[]> {
  const res = await fetch(`${API_BASE}/strategies/decision-log?limit=${limit}`);
  if (!res.ok) throw new Error("Failed to fetch decision log");
  return res.json();
}

export async function fetchStrategyTrades(limit: number = 50): Promise<AutoTradeData[]> {
  const res = await fetch(`${API_BASE}/strategies/trades?limit=${limit}`);
  if (!res.ok) throw new Error("Failed to fetch strategy trades");
  return res.json();
}

export async function exitStrategyTrade(tradeId: string, reason: string = "MANUAL_UI_EXIT"): Promise<any> {
  const res = await fetch(`${API_BASE}/strategies/trades/${tradeId}/exit`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ reason }),
  });
  if (!res.ok) throw new Error("Failed to exit trade");
  return res.json();
}

export async function fetchTriggerDiagnostics(): Promise<TriggerDiagnosticsResponseData> {
  const res = await fetch(`${API_BASE}/strategies/triggers/diagnostics`);
  if (!res.ok) throw new Error("Failed to fetch trigger diagnostics");
  return res.json();
}

export async function fetchThresholdOverrides(): Promise<ThresholdOverridesData> {
  const res = await fetch(`${API_BASE}/strategies/overrides`);
  if (!res.ok) throw new Error("Failed to fetch threshold overrides");
  return res.json();
}

export async function updateThresholdOverrides(overrides: Partial<ThresholdOverridesData>): Promise<any> {
  const res = await fetch(`${API_BASE}/strategies/overrides`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(overrides),
  });
  if (!res.ok) throw new Error("Failed to update threshold overrides");
  return res.json();
}

export async function resetThresholdOverrides(): Promise<any> {
  const res = await fetch(`${API_BASE}/strategies/overrides/reset`, {
    method: "POST",
  });
  if (!res.ok) throw new Error("Failed to reset threshold overrides");
  return res.json();
}

export async function forceStrategyEntry(payload: {
  strategy?: string;
  direction?: "BULLISH" | "BEARISH";
  option_type?: "CALL" | "PUT";
  override_premium_cap?: number;
}): Promise<any> {
  const res = await fetch(`${API_BASE}/strategies/force-entry`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || err.reason || "Failed to force strategy entry");
  }
  return res.json();
}


// ==============================================================================
// Day Replay & Historical Simulation
// ==============================================================================

export interface SimulationBarSnapshotData {
  bar_index: number;
  timestamp: string;
  ist_time: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  spot: number;
  ema9_5m: number;
  ema20_5m: number;
  supertrend: string;
  adx_15m: number;
  rvol_5m: number;
  bb_width_percentile: number;
  strategy_a_phase: string;
  strategy_b_phase: string;
  active_trade_id?: string | null;
  event?: string | null;
  event_details?: string | null;
}

export interface SimulatedTradeRecordData {
  trade_id: string;
  strategy: string;
  direction: string;
  option_type: string;
  strike: number;
  contract_symbol: string;
  entry_time: string;
  entry_spot: number;
  entry_premium?: number | null;
  exit_time?: string | null;
  exit_spot?: number | null;
  exit_premium?: number | null;
  exit_reason?: string | null;
  initial_stop: number;
  initial_r_points: number;
  peak_r: number;
  realized_r: number;
  quantity: number;
  lots: number;
  gross_pnl?: number | null;
  net_pnl?: number | null;
  hold_duration_mins: number;
}

export interface SimulationResultData {
  limitation?: string;
  session_date: string;
  total_bars_evaluated: number;
  total_trades: number;
  winning_trades: number;
  losing_trades: number;
  win_rate_pct: number;
  total_pnl: number | null;
  net_pnl: number | null;
  total_realized_r: number;
  max_drawdown_pnl: number | null;
  profit_factor: number;
  trades: SimulatedTradeRecordData[];
  timeline: SimulationBarSnapshotData[];
  decision_logs: DecisionLogData[];
  replay_trigger_diagnostics?: Record<string, any>[];
  replay_manifests?: Record<string, any>[];
  replay_metadata?: Record<string, any>;
}

export interface SimulationRequestData {
  date?: string | null;
  instrument_id?: string;
  overrides?: Partial<ThresholdOverridesData>;
  capital?: number;
  bypass_window?: boolean;
  bypass_entry_window?: boolean;
  historical_source?: "BREEZE" | "KITE" | "LIVE" | "MIXED";
  max_trades_per_day?: number;
}

export async function runStrategySimulation(req: SimulationRequestData = {}): Promise<SimulationResultData> {
  const res = await fetch(`${API_BASE}/strategies/simulate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || "Failed to run strategy simulation");
  }
  return res.json();
}

export async function fetchSimulationAvailableDates(
  historicalSource: "BREEZE" | "KITE" = "BREEZE",
): Promise<string[]> {
  const res = await fetch(
    `${API_BASE}/strategies/simulate/available-dates?historical_source=${historicalSource}`,
  );
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    const detail = typeof err?.detail === "string" ? err.detail : "";
    const developmentDetail = process.env.NODE_ENV === "development" && detail ? `: ${detail}` : "";
    throw new Error(`Failed to fetch available simulation dates (${res.status})${developmentDetail}`);
  }
  const data = await res.json();
  return data.dates || [];
}
