"use client";

import { useQuery } from "@tanstack/react-query";
import BrokerConnections from "../features/broker_connections/BrokerConnections";

const AI_BASE = `${process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api/v1"}/ai`;

type AITrade = {
  trade_id: string;
  signal_id: string;
  symbol: string;
  instrument_id: string;
  expiry: string;
  quantity: number;
  entry_price: number;
  initial_stop: number;
  current_stop: number;
  target_price: number;
  peak_bid: number;
  last_bid: number | null;
  trailing_active: boolean;
  status: "OPEN" | "CLOSED";
  entry_time: string;
  last_quote_at: string | null;
  last_checked_at: string | null;
  data_status: string;
  exit_time: string | null;
  exit_price: number | null;
  exit_reason: string | null;
  pnl: number | null;
  unrealized_pnl: number | null;
};

type AISnapshot = {
  as_of?: string;
  underlying?: { available?: boolean; last_price?: number; source?: string };
  data_quality?: { quote_ready?: boolean };
  entry_data_ready?: boolean;
  blocking_reasons?: string[];
};

async function getAI<T>(path: string): Promise<T> {
  const response = await fetch(`${AI_BASE}${path}`, { cache: "no-store" });
  if (!response.ok) throw new Error(`AI endpoint ${path} returned HTTP ${response.status}`);
  return response.json() as Promise<T>;
}

const money = (n: number | null | undefined) =>
  n === null || n === undefined || !Number.isFinite(n)
    ? "—"
    : `₹${n.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

const time = (s: string | null | undefined) => s
  ? new Date(s).toLocaleString("en-IN", { timeZone: "Asia/Kolkata", hour12: false })
  : "—";

export default function AIPaperTerminal() {
  // The dashboard never submits orders or changes execution mode. Broker
  // connection buttons use the local AI route solely for daily authentication.
  const trades = useQuery({
    queryKey: ["ai-trades"],
    queryFn: () => getAI<AITrade[]>("/trades?limit=100"),
    refetchInterval: (query) =>
      (query.state.data || []).some((trade) => trade.status === "OPEN") ? 5000 : 15000,
  });
  const snapshot = useQuery({
    queryKey: ["ai-snapshot"],
    queryFn: () => getAI<AISnapshot>("/nifty/snapshot"),
    refetchInterval: 60000,
  });

  const all = trades.data || [];
  const active = all.filter((trade) => trade.status === "OPEN");
  const closed = all.filter((trade) => trade.status === "CLOSED");
  const totalPnl = closed.reduce((sum, trade) => sum + (trade.pnl || 0), 0);

  return (
    <main className="h-screen overflow-auto bg-[#0a0e17] text-slate-100 p-4 md:p-7 space-y-6">
      <header className="flex flex-wrap justify-between gap-4 items-center border-b border-slate-800 pb-5">
        <div>
          <p className="text-xs font-semibold tracking-[0.25em] text-blue-400">LOCAL · AI TRADE MONITOR</p>
          <h1 className="text-2xl md:text-3xl font-bold mt-1">NIFTY AI Trade Monitor</h1>
          <p className="text-sm text-slate-400 mt-1">Signals arrive through the AI API. The backend manages simulated stops, trailing, and exits.</p>
        </div>
        <span className="rounded-md border border-slate-700 bg-slate-900 px-3 py-2 text-xs font-medium text-slate-300">
          AI PAPER · No manual trading controls
        </span>
      </header>

      <BrokerConnections />

      <section className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <Stat title="NIFTY" value={money(snapshot.data?.underlying?.last_price)} detail={snapshot.data?.underlying?.source || "No live index quote"} />
        <Stat title="OPEN AI TRADES" value={String(active.length)} detail="Managed by independent backend loop" />
        <Stat title="CLOSED AI TRADES" value={String(closed.length)} detail="From AI trade journal" />
        <Stat title="CLOSED GROSS P&L" value={money(totalPnl)} detail="Simulated, before charges" />
      </section>

      {(trades.error || snapshot.error) && (
        <div role="alert" className="rounded border border-amber-800 bg-amber-950/30 p-3 text-sm text-amber-200">
          {trades.error instanceof Error ? trades.error.message : ""}
          {snapshot.error instanceof Error ? ` ${snapshot.error.message}` : ""}
          {" "}The display does not submit orders or simulate missing data.
        </div>
      )}

      <section className="space-y-3">
        <div className="flex justify-between items-center">
          <h2 className="text-lg font-semibold">Open AI Trades</h2>
          <span className="text-xs text-slate-400">Trade status refresh: 5s while open</span>
        </div>
        {trades.isPending ? <Empty message="Loading AI trade journal…" /> :
          active.length === 0 ? <Empty message="No open AI trades. Accepted AI signals will appear here automatically." /> :
          <div className="grid gap-3">
            {active.map((trade) => <TradeCard key={trade.trade_id} trade={trade} />)}
          </div>}
      </section>

      <section className="space-y-3">
        <h2 className="text-lg font-semibold">AI Trade History</h2>
        {closed.length === 0 ? <Empty message="No closed AI trades yet." /> :
          <div className="overflow-x-auto border border-slate-800 rounded-lg">
            <table className="w-full min-w-[800px] text-sm text-left">
              <thead className="bg-slate-900 text-slate-400 text-xs uppercase">
                <tr>{["Contract", "Entry (IST)", "Exit (IST)", "Qty", "Entry ask", "Exit bid", "Gross P&L", "Exit reason"].map((label) =>
                  <th key={label} className="px-4 py-3">{label}</th>)}</tr>
              </thead>
              <tbody>
                {closed.map((trade) => (
                  <tr key={trade.trade_id} className="border-t border-slate-800">
                    <td className="px-4 py-3 font-medium">{trade.symbol}</td>
                    <td className="px-4 py-3">{time(trade.entry_time)}</td>
                    <td className="px-4 py-3">{time(trade.exit_time)}</td>
                    <td className="px-4 py-3">{trade.quantity}</td>
                    <td className="px-4 py-3">{money(trade.entry_price)}</td>
                    <td className="px-4 py-3">{money(trade.exit_price)}</td>
                    <td className={`px-4 py-3 ${(trade.pnl || 0) >= 0 ? "text-emerald-400" : "text-rose-400"}`}>{money(trade.pnl)}</td>
                    <td className="px-4 py-3 text-slate-300">{trade.exit_reason || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>}
      </section>
      <footer className="text-xs text-slate-500 pb-5">
        Source: /api/v1/ai/nifty/snapshot and /api/v1/ai/trades. Broker login/status also use /api/v1/ai/broker/* only.
        Quote timestamps reflect Kite exchange observations, not browser refresh time.
        PAPER entries/exits are simulated, not broker fills.
        Snapshot: {time(snapshot.data?.as_of)} · Market data: {snapshot.data?.data_quality?.quote_ready ? "Ready" : "Unavailable / not ready"}.
      </footer>
    </main>
  );
}

function Stat({ title, value, detail }: { title: string; value: string; detail: string }) {
  return <div className="border border-slate-800 bg-[#101827] rounded-lg p-4">
    <p className="text-xs tracking-wider text-slate-400">{title}</p>
    <p className="text-xl font-semibold mt-2">{value}</p>
    <p className="text-xs text-slate-500 mt-1">{detail}</p>
  </div>;
}

function Empty({ message }: { message: string }) {
  return <div className="border border-dashed border-slate-700 rounded-lg p-8 text-center text-sm text-slate-400">{message}</div>;
}

function TradeCard({ trade }: { trade: AITrade }) {
  const items: [string, string][] = [
    ["Entry ask", money(trade.entry_price)],
    ["Last observed bid", money(trade.last_bid)],
    ["Initial stop", money(trade.initial_stop)],
    ["Current trailing stop", money(trade.current_stop)],
    ["Profit target", money(trade.target_price)],
    ["Peak observed bid", money(trade.peak_bid)],
    ["Gross unrealized P&L", money(trade.unrealized_pnl)],
    ["Last Kite quote (IST)", time(trade.last_quote_at)],
  ];
  return <article className="rounded-lg border border-slate-700 bg-[#101827] p-4 space-y-4">
    <div className="flex flex-wrap justify-between gap-2">
      <div>
        <h3 className="font-semibold text-lg">{trade.symbol}</h3>
        <p className="text-xs text-slate-400 mt-1">Quantity: {trade.quantity} · Expiry: {trade.expiry} · Entered: {time(trade.entry_time)}</p>
        <p className="text-xs text-slate-500 mt-1 break-all">Trade ID: {trade.trade_id}</p>
      </div>
      <span className="text-xs px-3 py-1 rounded border border-blue-700 bg-blue-950 text-blue-300 self-start">
        {trade.trailing_active ? "TRAILING ACTIVE" : "INITIAL STOP ACTIVE"}
      </span>
    </div>
    <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
      {items.map(([name, value]) => <div key={name}>
        <p className="text-xs text-slate-400">{name}</p>
        <p className="font-mono mt-1">{value}</p>
      </div>)}
    </div>
    <p className={`text-xs ${trade.data_status === "VALID" ? "text-slate-400" : "text-amber-300"}`}>
      Quote status: {trade.data_status} · Last monitor check: {time(trade.last_checked_at)}
      {trade.data_status !== "VALID" ? " · Exit protection cannot be verified until fresh quotes resume." : ""}
    </p>
  </article>;
}
