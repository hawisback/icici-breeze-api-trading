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


