"use client";

import { useQuery } from "@tanstack/react-query";
import BrokerConnections from "../features/broker_connections/BrokerConnections";

const AI_BASE = `${process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api/v1"}/ai`;

type ExecutionOrder = {
  order_id: string; request_id: string; kind: "ENTRY" | "STOP" | "EXIT";
  mode: "PAPER" | "LIVE"; parent_order_id: string | null;
  symbol: string; quantity: number; price: number; trigger_price: number | null;
  status: string; filled_quantity: number; average_price: number;
  broker_order_id: string | null; message: string | null; created_at: string;
};
type Position = {
  stock_code: string; quantity: number; average_price: number;
  ltp: number; pnl: number; exchange_code: string;
};
type Status = {
  mode: "PAPER" | "LIVE"; execution_enabled: boolean;
  live_capability_enabled: boolean; live_authorized: boolean;
  live_authorization_reason: string; market_data_backend: string;
  execution_broker: string; mode_resets_to_paper_on_restart: boolean;
};
type Monitor = {
  status: Status; orders: ExecutionOrder[]; positions: Position[];
  positions_error?: string;
};

async function fetchAI<T>(route: string): Promise<T> {
  const res = await fetch(`${AI_BASE}${route}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`Execution monitor HTTP ${res.status}`);
  return res.json() as Promise<T>;
}

const inr = (value: number | null | undefined) =>
  value === null || value === undefined || !Number.isFinite(value)
    ? "—" : `₹${value.toLocaleString("en-IN", { maximumFractionDigits: 2, minimumFractionDigits: 2 })}`;

export default function AIExecutionMonitor() {
  const monitor = useQuery({
    queryKey: ["ai-execution-monitor"],
    queryFn: () => fetchAI<Monitor>("/execution/monitor"),
    refetchInterval: 3000,
  });
  const quote = useQuery({
    queryKey: ["ai-execution-nifty"],
    queryFn: () => fetchAI<{underlying?: { last_price?: number; source?: string }}>("/nifty/snapshot"),
    refetchInterval: 30000,
  });
  const data = monitor.data;
  const live = data?.status.mode === "LIVE";
  const orders = data?.orders || [];
  const positions = data?.positions || [];
  const liveOpen = orders.filter(o => o.mode === "LIVE" && o.kind === "ENTRY" &&
    o.filled_quantity > 0 && !orders.some(exit => exit.parent_order_id === o.order_id &&
      exit.kind === "EXIT" && exit.status === "FILLED")).length;
  const attention = orders.filter(o => ["UNKNOWN", "REJECTED", "FAILED_SAFE"].includes(o.status)).length;

  return (
    <main className="min-h-screen bg-[#0a0e17] text-slate-100 p-4 md:p-7 space-y-6">
      <header className="flex flex-wrap justify-between gap-4 items-center border-b border-slate-800 pb-5">
        <div>
          <p className="text-xs font-semibold tracking-[0.25em] text-blue-400">LOCAL · EXTERNAL AI EXECUTION MONITOR</p>
          <h1 className="text-2xl md:text-3xl font-bold mt-1">NIFTY AI Trade Monitor</h1>
          <p className="text-sm text-slate-400 mt-1">
            The AI controls entries, stop-loss adjustments, exits and trading mode.
            This screen is read-only.
          </p>
        </div>
        <div className={live
          ? "rounded-md border border-rose-600 bg-rose-950 px-4 py-2 text-sm text-rose-200"
          : "rounded-md border border-blue-700 bg-blue-950 px-4 py-2 text-sm text-blue-200"}>
          <strong>{data?.status.mode || "LOADING"}</strong>
          <span className="ml-2 opacity-70">· Kite execution</span>
        </div>
      </header>

      <BrokerConnections />

      <section className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <Stat label="NIFTY" value={inr(quote.data?.underlying?.last_price)} detail={quote.data?.underlying?.source || "Quote unavailable"}/>
        <Stat label="MODE" value={data?.status.mode || "—"} detail={data?.status.market_data_backend ? `Market data: ${data.status.market_data_backend}` : "Not connected"}/>
        <Stat label="OPEN POSITIONS" value={String(positions.filter(p => p.quantity !== 0).length)} detail={live ? "Kite broker positions" : "Paper simulated positions"}/>
        <Stat label="ORDERS NEED ATTENTION" value={String(attention)} detail="Unknown or rejected responses"/>
      </section>

      {(monitor.error || data?.positions_error) && (
        <div role="alert" className="rounded border border-amber-700 bg-amber-950/30 p-3 text-sm text-amber-200">
          {monitor.error instanceof Error ? monitor.error.message : ""}
          {data?.positions_error ? ` Positions: ${data.positions_error}.` : ""}
          {" "}Broker state may be unverified; do not assume positions are flat.
        </div>
      )}

      {live && liveOpen > 0 && (
        <div role="alert" className="rounded border border-amber-700 bg-amber-950/30 p-3 text-sm text-amber-200">
          Check Kite order status and protective STOP coverage for every LIVE fill.
          An accepted entry alone does not prove that a stop exists or has executed.
        </div>
      )}

      <section className="rounded-lg border border-slate-800 bg-[#101827] p-4 space-y-2">
        <h2 className="font-semibold">Execution state</h2>
        <p className="text-sm text-slate-400">
          LIVE capability: {data?.status.live_capability_enabled ? "Configured" : "Disabled"} ·
          AI LIVE switch: {data?.status.live_authorized ? "Authorized" : "Disabled"} ·
          Mode resets to PAPER after a backend restart.
        </p>
        <p className="text-xs text-slate-500">
          PAPER LIMIT orders fill immediately at the submitted price in the test adapter;
          PAPER stops remain pending and do not trigger automatically. The AI must command the exit.
        </p>
      </section>

      <section className="space-y-3">
        <div className="flex flex-wrap justify-between items-baseline gap-2">
          <h2 className="text-lg font-semibold">Broker order journal</h2>
          <span className="text-xs text-slate-500">Refresh every 3 seconds · Actual Kite statuses in LIVE</span>
        </div>
        {orders.length === 0
          ? <Empty text="No AI-controlled execution orders yet."/>
          : <div className="overflow-x-auto border border-slate-800 rounded-lg">
            <table className="w-full min-w-[900px] text-left text-sm">
              <thead className="bg-slate-900 text-xs text-slate-400 uppercase">
                <tr>{["Mode", "Kind", "Contract", "Quantity / filled", "Limit", "Trigger", "Status", "Broker ID"].map(c =>
                  <th className="px-3 py-3" key={c}>{c}</th>)}</tr>
              </thead>
              <tbody>
                {orders.map(o => (
                  <tr key={o.order_id} className="border-t border-slate-800">
                    <td className="px-3 py-3">{o.mode}</td>
                    <td className="px-3 py-3">{o.kind}</td>
                    <td className="px-3 py-3 font-mono text-xs">{o.symbol}</td>
                    <td className="px-3 py-3">{o.quantity} / {o.filled_quantity}</td>
                    <td className="px-3 py-3">{inr(o.price)}</td>
                    <td className="px-3 py-3">{inr(o.trigger_price)}</td>
                    <td className={["UNKNOWN", "REJECTED"].includes(o.status) ? "px-3 py-3 text-amber-300" : "px-3 py-3"}>
                      {o.status}
                    </td>
                    <td className="px-3 py-3 font-mono text-xs">{o.broker_order_id || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>}
      </section>

      <section className="space-y-3">
        <h2 className="text-lg font-semibold">{live ? "Kite positions" : "Paper positions"}</h2>
        {positions.length === 0
          ? <Empty text="No open positions in the current execution mode."/>
          : <div className="overflow-x-auto border border-slate-800 rounded-lg">
            <table className="w-full min-w-[650px] text-sm text-left">
              <thead className="bg-slate-900 text-xs text-slate-400 uppercase">
                <tr>{["Symbol", "Quantity", "Average", "LTP", "Gross P&L"].map(c =>
                  <th key={c} className="px-4 py-3">{c}</th>)}</tr>
              </thead>
              <tbody>{positions.map(p => (
                <tr key={p.stock_code} className="border-t border-slate-800">
                  <td className="px-4 py-3 font-mono">{p.stock_code}</td>
                  <td className="px-4 py-3">{p.quantity}</td>
                  <td className="px-4 py-3">{inr(p.average_price)}</td>
                  <td className="px-4 py-3">{inr(p.ltp)}</td>
                  <td className="px-4 py-3">{inr(p.pnl)}</td>
                </tr>
              ))}</tbody>
            </table>
          </div>}
      </section>
      <footer className="text-xs text-slate-500 pb-5">
        The UI never submits an order or selects LIVE. Only the local external AI execution API
        may request mode changes or broker writes with its execution key.
      </footer>
    </main>
  );
}

function Stat({label, value, detail}: {label: string; value: string; detail: string}) {
  return <div className="border border-slate-800 bg-[#101827] rounded-lg p-4">
    <p className="text-xs tracking-wider text-slate-400">{label}</p>
    <p className="text-xl font-semibold mt-2">{value}</p>
    <p className="text-xs text-slate-500 mt-1">{detail}</p>
  </div>;
}
function Empty({text}: {text: string}) {
  return <div className="border border-dashed border-slate-700 rounded-lg p-8 text-center text-sm text-slate-400">{text}</div>;
}
