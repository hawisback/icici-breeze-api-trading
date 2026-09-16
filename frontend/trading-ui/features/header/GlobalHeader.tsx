"use client";

import React from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Activity,
  AlertOctagon,
  CheckCircle2,
  Lock,
  Power,
  RefreshCw,
  ShieldAlert,
  Wifi,
  WifiOff,
} from "lucide-react";
import { fetchPnLSummary, fetchQuotes, fetchSystemHealth } from "@/lib/api";
import { useTradingWebSocket } from "@/lib/useWebSocket";
import { useTradingStore } from "@/stores/useTradingStore";

export function GlobalHeader() {
  const { connected: wsConnected } = useTradingWebSocket();
  const {
    tradingMode,
    setTradingMode,
    safetyMode,
    setKillSwitchModalOpen,
  } = useTradingStore();

  const { data: health } = useQuery({
    queryKey: ["system_health"],
    queryFn: fetchSystemHealth,
    refetchInterval: 5000,
  });

  const { data: quotes } = useQuery({
    queryKey: ["quotes"],
    queryFn: fetchQuotes,
    refetchInterval: 2000,
  });

  const { data: pnl } = useQuery({
    queryKey: ["pnl_summary"],
    queryFn: fetchPnLSummary,
    refetchInterval: 2000,
  });

  const niftyQuote = quotes?.find((q) => q.symbol === "NIFTY 50" || q.instrument_id === "INST-NIFTY-INDEX");

  const brokerConnected = health?.services?.broker_session === "CONNECTED";
  const marketFeedStatus = health?.services?.market_feed || "LIVE";

  return (
    <header className="h-14 bg-[#0f172a] border-b border-[#1e293b] px-4 flex items-center justify-between select-none text-xs">
      {/* Brand & Market Ticker */}
      <div className="flex items-center space-x-6">
        <div className="flex items-center space-x-2">
          <div className="w-2.5 h-2.5 rounded-full bg-blue-500 animate-pulse" />
          <span className="font-bold tracking-wider text-sm bg-gradient-to-r from-blue-400 to-teal-300 bg-clip-text text-transparent">
            ICICI BREEZE TERMINAL
          </span>
          <span className="text-[10px] px-1.5 py-0.5 bg-slate-800 text-slate-400 rounded font-mono border border-slate-700">
            v2.0
          </span>
        </div>

        {/* NIFTY 50 Live Ticker */}
        <div className="hidden md:flex items-center space-x-3 px-3 py-1 bg-[#1e293b]/50 rounded border border-slate-800">
          <span className="text-slate-400 font-medium">NIFTY 50</span>
          <span className="font-mono font-semibold text-slate-100">
            {niftyQuote ? niftyQuote.last_price.toFixed(2) : "24,850.50"}
          </span>
          <span
            className={`font-mono text-[11px] font-medium ${
              (niftyQuote?.change_pct || 0) >= 0 ? "text-emerald-400" : "text-rose-400"
            }`}
          >
            {(niftyQuote?.change_pct || 0) >= 0 ? "+" : ""}
            {niftyQuote ? niftyQuote.change_pct.toFixed(2) : "+0.20"}%
          </span>
        </div>

        {/* Day P&L */}
        <div className="hidden lg:flex items-center space-x-2 px-3 py-1 bg-[#1e293b]/50 rounded border border-slate-800">
          <span className="text-slate-400">Day P&L:</span>
          <span
            className={`font-mono font-semibold ${
              (pnl?.day_pnl || 0) >= 0 ? "text-emerald-400" : "text-rose-400"
            }`}
          >
            ₹{pnl?.day_pnl ? pnl.day_pnl.toFixed(2) : "0.00"}
          </span>
          <span className="text-[11px] text-slate-500">
            ({pnl?.open_positions_count || 0} open)
          </span>
        </div>
      </div>

      {/* Global Status Badges & Controls */}
      <div className="flex items-center space-x-3">
        {/* Broker Session */}
        <div
          className={`flex items-center space-x-1.5 px-2 py-1 rounded font-mono border ${
            brokerConnected
              ? "bg-emerald-950/40 text-emerald-400 border-emerald-800/60"
              : "bg-slate-900 text-slate-400 border-slate-800"
          }`}
          title="Breeze Broker Session"
        >
          <Lock className="w-3 h-3" />
          <span>{brokerConnected ? "BROKER CONNECTED" : "BROKER READY"}</span>
        </div>

        {/* Market Feed Status */}
        <div
          className={`flex items-center space-x-1.5 px-2 py-1 rounded font-mono border ${
            marketFeedStatus === "LIVE"
              ? "bg-teal-950/40 text-teal-400 border-teal-800/60"
              : "bg-amber-950/40 text-amber-400 border-amber-800/60"
          }`}
          title="Market Data Stream Health"
        >
          {wsConnected ? <Wifi className="w-3 h-3" /> : <WifiOff className="w-3 h-3" />}
          <span>FEED: {marketFeedStatus}</span>
        </div>

        {/* Trading Mode Switcher */}
        <div className="flex items-center bg-slate-900 border border-slate-800 rounded p-0.5">
          {(["PAPER", "SHADOW", "LIVE"] as const).map((m) => (
            <button
              key={m}
              onClick={() => setTradingMode(m)}
              className={`px-2.5 py-0.5 rounded text-[10px] font-bold tracking-wider transition ${
                tradingMode === m
                  ? m === "LIVE"
                    ? "bg-rose-600 text-white shadow"
                    : "bg-blue-600 text-white shadow"
                  : "text-slate-400 hover:text-slate-200"
              }`}
            >
              {m}
            </button>
          ))}
        </div>

        {/* Safety Mode Indicator */}
        <div
          className={`flex items-center space-x-1.5 px-2.5 py-1 rounded font-semibold tracking-wider text-[10px] border ${
            safetyMode === "NORMAL"
              ? "bg-emerald-950/30 text-emerald-400 border-emerald-800/50"
              : "bg-rose-950/40 text-rose-300 border-rose-800/80 animate-pulse"
          }`}
        >
          <ShieldAlert className="w-3.5 h-3.5" />
          <span>{safetyMode}</span>
        </div>

        {/* Emergency Kill Switch Trigger */}
        <button
          onClick={() => setKillSwitchModalOpen(true)}
          className="flex items-center space-x-1 px-2.5 py-1 bg-rose-600/20 hover:bg-rose-600/40 text-rose-300 border border-rose-500/50 rounded font-semibold transition active:scale-95"
        >
          <AlertOctagon className="w-3.5 h-3.5 text-rose-400" />
          <span>KILL SWITCH</span>
        </button>
      </div>
    </header>
  );
}

