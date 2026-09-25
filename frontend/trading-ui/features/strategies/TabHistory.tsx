"use client";

import React from "react";
import { useQuery } from "@tanstack/react-query";
import { ArrowDownRight, ArrowUpRight, BarChart2, CheckCircle2, History, RefreshCw, TrendingUp } from "lucide-react";
import { AutoTradeData, StrategyStatusData, fetchStrategyTrades } from "../../lib/api";
import { formatISTTime } from "../../lib/time";

interface TabHistoryProps {
  status?: StrategyStatusData | null;
}

export const TabHistory: React.FC<TabHistoryProps> = ({ status }) => {
  const {
    data: trades = [],
    isLoading,
    refetch: loadTrades,
  } = useQuery<AutoTradeData[]>({
    queryKey: ["strategy_trades"],
    queryFn: () => fetchStrategyTrades(50),
    refetchInterval: 3000,
    refetchIntervalInBackground: true,
    refetchOnWindowFocus: true,
  });

  const closedTrades = trades.filter((t) => t.state === "CLOSED");
  const totalTrades = closedTrades.length;
  const winningTrades = closedTrades.filter((t) => (t.net_pnl || 0) > 0);
  const winRate = totalTrades > 0 ? (winningTrades.length / totalTrades) * 100 : 0;
  const totalNetPnL = closedTrades.reduce((acc, t) => acc + (t.net_pnl || 0), 0);
  const totalR = closedTrades.reduce((acc, t) => acc + (t.realized_r || 0), 0);
  const candidateRows = [
    ...(status?.strategy_c_paper?.paper_trades || []).map((row) => {
      const isActive =
        status?.strategy_c_paper?.active_paper_trade?.signal_id === row.signal_id;
      return {
        key: `C-${row.signal_id || row.entry_time || Math.random()}`,
        strategy: "C",
        time: row.entry_time || row.entry_observed_at,
        optionType: row.direction || row.option_type || "--",
        contract:
          row.selected_contract?.symbol ||
          row.selected_contract?.instrument_id ||
          "--",
        status: row.paper_status || "--",
        stop: isActive
          ? status?.strategies.di_continuation.current_trailing_stop
          : row.underlying_stop,
        r: isActive
          ? status?.strategies.di_continuation.current_r
          : row.paper_underlying_realized_r,
        netPnl: row.paper_net_pnl,
      };
    }),
    ...(status?.strategy_d_paper?.paper_trades || []).map((row) => {
      const isActive =
        status?.strategy_d_paper?.active_paper_trade?.signal_id === row.signal_id;
      return {
        key: `D-${row.signal_id || row.entry_observed_at || Math.random()}`,
        strategy: "D",
        time: row.entry_observed_at || row.signal?.timestamp,
        optionType: row.signal?.option_type || "--",
        contract:
          row.selected_contract?.symbol ||
          row.selected_contract?.instrument_id ||
          "--",
        status: row.paper_status || "--",
        stop:
          row.current_underlying_stop ??
          row.signal?.initial_stop ??
          (isActive
            ? status?.strategies.sr_momentum_breakout.current_trailing_stop
            : null),
        r:
          row.current_r ??
          row.paper_underlying_realized_r ??
          (isActive ? status?.strategies.sr_momentum_breakout.current_r : null),
        netPnl: row.paper_net_pnl,
      };
    }),
  ].sort(
    (a, b) =>
      new Date(b.time || 0).getTime() - new Date(a.time || 0).getTime(),
  );

  return (
    <div className="space-y-6">
      {/* 1. Performance KPI Cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <div className="bg-slate-900/90 border border-slate-800 rounded-xl p-4 shadow-lg">
          <div className="text-[11px] text-slate-400 uppercase tracking-wider mb-1 flex items-center gap-1.5">
            <History className="w-3.5 h-3.5 text-blue-400" />
            Total Closed Trades
          </div>
          <div className="text-xl font-extrabold text-slate-100">{totalTrades}</div>
          <div className="text-xs text-slate-400">Intraday automated executions</div>
        </div>

        <div className="bg-slate-900/90 border border-slate-800 rounded-xl p-4 shadow-lg">
          <div className="text-[11px] text-slate-400 uppercase tracking-wider mb-1 flex items-center gap-1.5">
            <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400" />
            Win Rate
          </div>
          <div className="text-xl font-extrabold text-emerald-400">
            {winRate.toFixed(1)}%
          </div>
          <div className="text-xs text-slate-400">
            {winningTrades.length} Wins / {totalTrades - winningTrades.length} Losses
          </div>
        </div>

        <div className="bg-slate-900/90 border border-slate-800 rounded-xl p-4 shadow-lg">
          <div className="text-[11px] text-slate-400 uppercase tracking-wider mb-1 flex items-center gap-1.5">
            <TrendingUp className="w-3.5 h-3.5 text-cyan-400" />
            Total Realized R
          </div>
          <div
            className={`text-xl font-extrabold ${
              totalR >= 0 ? "text-cyan-400" : "text-rose-400"
            }`}
          >
            {totalR >= 0 ? "+" : ""}{totalR.toFixed(2)}R
          </div>
          <div className="text-xs text-slate-400">
            Avg {totalTrades > 0 ? (totalR / totalTrades).toFixed(2) : "0.00"}R per trade
          </div>
        </div>

        <div className="bg-slate-900/90 border border-slate-800 rounded-xl p-4 shadow-lg">
          <div className="text-[11px] text-slate-400 uppercase tracking-wider mb-1 flex items-center gap-1.5">
            <BarChart2 className="w-3.5 h-3.5 text-amber-400" />
            Net Realized PnL
          </div>
          <div
            className={`text-xl font-extrabold ${
              totalNetPnL >= 0 ? "text-emerald-400" : "text-rose-400"
            }`}
          >
            {totalNetPnL >= 0 ? "+" : ""}₹{totalNetPnL.toLocaleString("en-IN", { minimumFractionDigits: 2 })}
          </div>
          <div className="text-xs text-slate-400">Post brokerage & taxes</div>
        </div>
      </div>

      {/* 2. Trade History Table */}
      <div className="bg-slate-900/90 border border-slate-800 rounded-xl overflow-hidden shadow-lg">
        <div className="px-5 py-3.5 bg-slate-950 border-b border-slate-800 flex items-center justify-between">
          <h4 className="text-sm font-bold text-slate-200 uppercase tracking-wider flex items-center gap-2">
            <History className="w-4 h-4 text-cyan-400" />
            Automated Trade Execution History
          </h4>

          <button
            onClick={() => loadTrades()}
            disabled={isLoading}
            className="p-1.5 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded text-xs transition-colors"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${isLoading ? "animate-spin" : ""}`} />
          </button>
        </div>

        <div className="overflow-x-auto">
          {trades.length === 0 ? (
            <div className="p-12 text-center text-slate-500 text-xs">
              No historical automated trades recorded yet.
            </div>
          ) : (
            <table className="w-full text-xs text-left">
              <thead className="bg-slate-950/80 text-slate-400 border-b border-slate-800">
                <tr>
                  <th className="py-2.5 px-4">TIME</th>
                  <th className="py-2.5 px-4">STRATEGY</th>
                  <th className="py-2.5 px-4">CONTRACT</th>
                  <th className="py-2.5 px-4">TYPE</th>
                  <th className="py-2.5 px-4">QTY</th>
                  <th className="py-2.5 px-4">ENTRY</th>
                  <th className="py-2.5 px-4">EXIT</th>
                  <th className="py-2.5 px-4">LIFECYCLE / EXIT</th>
                  <th className="py-2.5 px-4">A EVIDENCE</th>
                  <th className="py-2.5 px-4 text-right">R-MULTIPLE</th>
                  <th className="py-2.5 px-4 text-right">NET PnL</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/60 font-mono">
                {trades.map((trade) => {
                  const isClosed = trade.state === "CLOSED";
                  const netPnL = trade.net_pnl ?? 0;
                  const realizedR = trade.realized_r ?? trade.current_r;

                  return (
                    <tr key={trade.trade_id} className="hover:bg-slate-850/40 transition-colors">
                      <td className="py-2.5 px-4 text-slate-400">
                        {formatISTTime(trade.entry_time)}
                      </td>

                      <td className="py-2.5 px-4 font-sans font-semibold text-slate-200">
                        {trade.strategy === "TREND_PULLBACK" ? "Pullback Cont." : "Vol. Breakout"}
                      </td>

                      <td className="py-2.5 px-4 font-bold text-slate-100">
                        {trade.contract_symbol}
                      </td>

                      <td className="py-2.5 px-4 font-sans">
                        <span
                          className={`px-1.5 py-0.5 rounded text-[10px] font-bold ${
                            trade.option_type === "CALL"
                              ? "bg-emerald-500/20 text-emerald-300"
                              : "bg-rose-500/20 text-rose-300"
                          }`}
                        >
                          {trade.option_type}
                        </span>
                      </td>

                      <td className="py-2.5 px-4 text-slate-300">
                        {trade.quantity} ({trade.lots}L)
                      </td>

                      <td className="py-2.5 px-4 text-slate-200">
                        ₹{trade.entry_option_price.toFixed(2)}
                      </td>

                      <td className="py-2.5 px-4 text-slate-200">
                        {trade.exit_option_price ? `₹${trade.exit_option_price.toFixed(2)}` : "—"}
                      </td>

                      <td className="py-2.5 px-4 font-sans text-slate-400 max-w-xs">
                        <div className="truncate">{trade.pending_exit_reason || trade.exit_reason || (isClosed ? "CLOSED" : "ACTIVE")}</div>
                        {trade.strategy === "TREND_PULLBACK" && trade.underlying_outcome_status && <div className="text-[10px] text-cyan-400 truncate">{trade.underlying_outcome_status}</div>}
                      </td>
                      <td className="py-2.5 px-4 font-sans text-[10px] text-slate-400">
                        {trade.strategy === "TREND_PULLBACK" ? <><div>FUT {(trade.underlying_entry_price ?? trade.entry_spot_price).toFixed(1)} → {trade.underlying_exit_price?.toFixed(1) ?? "…"}</div><div>T1 {trade.t1_reached ? "✓" : "—"} · rem {trade.remaining_quantity ?? 0} · data {trade.option_data_status || "—"}</div></> : "—"}
                      </td>

                      <td className="py-2.5 px-4 text-right font-bold">
                        <span
                          className={
                            realizedR >= 0 ? "text-emerald-400" : "text-rose-400"
                          }
                        >
                          {realizedR >= 0 ? "+" : ""}{realizedR.toFixed(2)}R
                        </span>
                      </td>

                      <td className="py-2.5 px-4 text-right font-bold font-sans">
                        {isClosed ? (
                          <span
                            className={
                              netPnL >= 0 ? "text-emerald-400" : "text-rose-400"
                            }
                          >
                            {netPnL >= 0 ? "+" : ""}₹{netPnL.toFixed(2)}
                          </span>
                        ) : (
                          <span className="text-cyan-400 font-mono">
                            {trade.unrealized_pnl >= 0 ? "+" : ""}₹{trade.unrealized_pnl.toFixed(2)}
                          </span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>
      {/* 3. Frozen candidate paper evidence */}
      <div className="bg-slate-900/90 border border-slate-800 rounded-xl overflow-hidden shadow-lg">
        <div className="px-5 py-3.5 bg-slate-950 border-b border-slate-800">
          <h4 className="text-sm font-bold text-slate-200 uppercase tracking-wider flex items-center gap-2">
            <BarChart2 className="w-4 h-4 text-violet-400" />
            Strategy C / D Paper Candidate Evidence
          </h4>
          <p className="text-[10px] text-slate-500 mt-1">Isolated candidate observations; these are intentionally separate from A/B ActiveTrade history.</p>
        </div>
        <div className="overflow-x-auto">
          {candidateRows.length === 0 ? (
            <div className="p-8 text-center text-slate-500 text-xs">No C/D paper candidate trades recorded yet.</div>
          ) : (
            <table className="w-full text-xs text-left">
              <thead className="bg-slate-950/80 text-slate-400 border-b border-slate-800">
                <tr>
                  <th className="py-2.5 px-4">TIME</th>
                  <th className="py-2.5 px-4">STRATEGY</th>
                  <th className="py-2.5 px-4">TYPE</th>
                  <th className="py-2.5 px-4">CONTRACT</th>
                  <th className="py-2.5 px-4">PAPER STATUS</th>
                  <th className="py-2.5 px-4 text-right">STOP</th>
                  <th className="py-2.5 px-4 text-right">R</th>
                  <th className="py-2.5 px-4 text-right">NET PnL</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/60">
                {candidateRows.map((row) => (
                  <tr key={row.key} className="hover:bg-slate-850/40">
                    <td className="py-2.5 px-4 text-slate-400 font-mono">{row.time ? formatISTTime(row.time) : "--"}</td>
                    <td className="py-2.5 px-4 font-bold text-violet-300">Strategy {row.strategy}</td>
                    <td className="py-2.5 px-4"><span className={`px-1.5 py-0.5 rounded text-[10px] font-bold ${row.optionType === "CALL" ? "bg-emerald-500/20 text-emerald-300" : "bg-rose-500/20 text-rose-300"}`}>{row.optionType}</span></td>
                    <td className="py-2.5 px-4 text-slate-200 font-mono">{row.contract}</td>
                    <td className="py-2.5 px-4 text-slate-300">{row.status}</td>
                    <td className="py-2.5 px-4 text-right font-mono text-rose-300">{row.stop == null ? "--" : `₹${Number(row.stop).toFixed(2)}`}</td>
                    <td className="py-2.5 px-4 text-right font-mono text-cyan-300">{row.r == null ? "--" : `${Number(row.r).toFixed(2)}R`}</td>
                    <td className={`py-2.5 px-4 text-right font-bold ${Number(row.netPnl || 0) >= 0 ? "text-emerald-400" : "text-rose-400"}`}>{row.netPnl == null ? "--" : `₹${Number(row.netPnl).toFixed(2)}`}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
      </div>
    </div>
  );
};

