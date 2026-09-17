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
  return data.map((c: any) => ({
    time: Math.floor(new Date(c.start_time).getTime() / 1000),
    open: c.open,
    high: c.high,
    low: c.low,
    close: c.close,
    volume: c.volume,
    source: c.source || "BREEZE",
    isoTime: c.start_time,
  }));
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

export async function fetchLoginUrl(): Promise<{ login_url: string; api_key: string }> {
  const res = await fetch(`${API_BASE}/broker/session/login-url`);
  if (!res.ok) throw new Error("Failed to fetch login URL");
  return res.json();
}

// ==============================================================================
// Auto-Trading Strategy API
// ==============================================================================

export interface StrategyStatusData {
  config: {
    mode: "PAPER" | "LIVE" | "DISABLED";
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
    };
    risk: {
      max_trade_capital: number;
      risk_per_trade_pct_of_account: number;
      max_daily_loss_r: number;
      max_daily_loss_pct: number;
      max_failed_trades_per_strategy: number;
      max_trades_per_day: number;
      max_concurrent_positions: number;
      cooldown_after_loss_min: number;
      option_hard_stop_pct: number;
    };
    session: {
      no_new_trade_before: string;
      no_new_trade_after: string;
      force_exit_time: string;
    };
    tunables: {
      evaluation_interval_sec: number;
      trend_pullback_enabled: boolean;
      volatility_breakout_enabled: boolean;
      adx_threshold: number;
      rvol_threshold: number;
      supertrend_period: number;
      supertrend_multiplier: number;
    };
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
  strategies: {
    trend_pullback: { enabled: boolean; state: string };
    volatility_breakout: { enabled: boolean; state: string };
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
  status: "PASSED" | "PENDING" | "BLOCKED";
  gap_description: string;
}

export interface StrategyTriggerDiagnosticsData {
  strategy: string;
  strategy_label: string;
  direction: "BULLISH" | "BEARISH";
  option_type: "CALL" | "PUT";
  overall_status: "READY_TO_TRIGGER" | "WAITING" | "BLOCKED";
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
  current_option_price: number;
  current_spot_price: number;
  current_trailing_stop: number;
  option_hard_stop_price: number;
  current_r: number;
  peak_r: number;
  mfe_points: number;
  mae_points: number;
  reversal_score: number;
  state: "OPEN_INITIAL_RISK" | "PROTECTED_BREAKEVEN" | "PROFIT_LOCKED" | "RUNNER_MODE" | "CLOSED";
  unrealized_pnl: number;
  exit_time?: string | null;
  exit_option_price?: number | null;
  exit_spot_price?: number | null;
  exit_reason?: string | null;
  gross_pnl?: number | null;
  net_pnl?: number | null;
  realized_r?: number | null;
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
  if (!res.ok) throw new Error("Failed to update strategy config");
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




