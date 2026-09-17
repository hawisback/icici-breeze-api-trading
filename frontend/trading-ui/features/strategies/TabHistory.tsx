"use client";

import React from "react";
import { useQuery } from "@tanstack/react-query";
import { ArrowDownRight, ArrowUpRight, BarChart2, CheckCircle2, History, RefreshCw, TrendingUp } from "lucide-react";
import { AutoTradeData, fetchStrategyTrades } from "../../lib/api";

export const TabHistory: React.FC = () => {
  const {
    data: trades = [],
    isLoading,
    refetch: loadTrades,
  } = useQuery<AutoTradeData[]>({
    queryKey: ["strategy_trades"],
    queryFn: () => fetchStrategyTrades(50),
    refetchInterval: 3000,
  });

  const closedTrades = trades.filter((t) => t.state === "CLOSED");
  const totalTrades = closedTrades.length;
  const winningTrades = closedTrades.filter((t) => (t.net_pnl || 0) > 0);
  const winRate = totalTrades > 0 ? (winningTrades.length / totalTrades) * 100 : 0;
  const totalNetPnL = closedTrades.reduce((acc, t) => acc + (t.net_pnl || 0), 0);
  const totalR = closedTrades.reduce((acc, t) => acc + (t.realized_r || 0), 0);

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
                  <th className="py-2.5 px-4">EXIT REASON</th>
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
                        {new Date(trade.entry_time).toLocaleTimeString([], {
                          hour: "2-digit",
                          minute: "2-digit",
                        })}
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

                      <td className="py-2.5 px-4 font-sans text-slate-400 max-w-xs truncate">
                        {trade.exit_reason || (isClosed ? "CLOSED" : "ACTIVE")}
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
      </div>
    </div>
  );
};

