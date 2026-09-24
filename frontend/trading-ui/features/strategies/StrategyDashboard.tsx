"use client";

import React, { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Activity,
  AlertOctagon,
  FileText,
  History,
  Play,
  RefreshCw,
  Shield,
  ShieldAlert,
  ShieldCheck,
  Sliders,
  Zap,
} from "lucide-react";
import {
  StrategyStatusData,
  armStrategySystem,
  evaluateStrategyNow,
  fetchStrategyStatus,
  setStrategyAutoTrade,
  toggleStrategyKillSwitch,
  updateStrategyConfig,
} from "../../lib/api";
import { TabOverview } from "./TabOverview";
import { TabParameters } from "./TabParameters";
import { TabDecisionLog } from "./TabDecisionLog";
import { TabHistory } from "./TabHistory";
import { TabReplaySimulation } from "./TabReplaySimulation";
import { TradingChart } from "../charts/TradingChart";

export const StrategyDashboard: React.FC = () => {
  const [activeTab, setActiveTab] = useState<"overview" | "parameters" | "decision_log" | "history" | "simulation">("overview");
  const [isEvaluating, setIsEvaluating] = useState(false);
  const [actionLoading, setActionLoading] = useState(false);

  const {
    data: status = null,
    refetch: loadStatus,
    isFetching: isRefreshing,
  } = useQuery<StrategyStatusData>({
    queryKey: ["strategy_status"],
    queryFn: fetchStrategyStatus,
    refetchInterval: 1500,
    refetchIntervalInBackground: true,
    refetchOnWindowFocus: true,
  });

  const liveEligibleStrategies = status
    ? Object.entries(status.strategies)
        .filter(([, item]) => item.enabled && item.live_trading_allowed)
        .map(([name]) => name)
    : [];
  const hasLiveEligibleStrategy = liveEligibleStrategies.length > 0;

  const handleArmToggle = async () => {
    if (!status) return;
    const targetArmed = !status.config.system_armed;
    if (targetArmed && status.config.mode === "LIVE") {
      if (!hasLiveEligibleStrategy) {
        alert(
          "No enabled strategy is promoted for LIVE execution. Platform LIVE mode does not override per-strategy execution policy.",
        );
        return;
      }
      const broker = status.market_data?.provider === "kite" ? "Zerodha Kite" : status.market_data?.provider === "breeze" ? "ICICI Breeze" : "the configured live broker";
      if (!confirm(`WARNING: Arming allows LIVE routing only for explicitly promoted strategies via ${broker}. Continue?`)) {
        return;
      }
    }
    try {
      setActionLoading(true);
      await armStrategySystem(targetArmed);
      await loadStatus();
    } catch (e: any) {
      alert(`Action failed: ${e.message}`);
    } finally {
      setActionLoading(false);
    }
  };

  const handleAutoTradeToggle = async () => {
    if (!status) return;
    try {
      setActionLoading(true);
      await setStrategyAutoTrade(!status.config.auto_trade_enabled);
      await loadStatus();
    } catch (e: any) {
      alert(`Action failed: ${e.message}`);
    } finally {
      setActionLoading(false);
    }
  };

  const handleKillSwitch = async () => {
    if (!status) return;
    const activate = !status.config.kill_switch;
    if (activate) {
      if (!confirm("EMERGENCY: Halt all auto-trading immediately and lock execution?")) return;
    }
    try {
      setActionLoading(true);
      await toggleStrategyKillSwitch(activate);
      await loadStatus();
    } catch (e: any) {
      alert(`Kill switch failed: ${e.message}`);
    } finally {
      setActionLoading(false);
    }
  };

  const handleEvaluateNow = async () => {
    try {
      setIsEvaluating(true);
      await evaluateStrategyNow();
      await loadStatus();
    } catch (e: any) {
      alert(`Evaluation failed: ${e.message}`);
    } finally {
      setIsEvaluating(false);
    }
  };

  const handleModeChange = async (newMode: "PAPER" | "LIVE") => {
    if (!status || status.config.mode === newMode) return;
    if (newMode === "LIVE") {
      if (!confirm(
        "Request platform LIVE mode? This does NOT promote any strategy. Each strategy remains limited by its server execution policy, and arming stays unavailable until at least one enabled strategy is LIVE-promoted.",
      )) return;
    }
    try {
      setActionLoading(true);
      await updateStrategyConfig({
        ...status.config,
        mode: newMode,
        system_armed: false, // Disarm by default on mode switch
      });
      await loadStatus();
    } catch (e: any) {
      alert(`Mode switch failed: ${e.message}`);
    } finally {
      setActionLoading(false);
    }
  };

  return (
    <div className="flex-1 flex flex-col h-full bg-[#0a0e17] overflow-y-auto">
      {/* Top Header Controls */}
      <div className="bg-slate-950 border-b border-slate-800 px-6 py-4 flex flex-col lg:flex-row lg:items-center justify-between gap-4 sticky top-0 z-20">
        <div>
          <div className="flex items-center gap-3">
            <h2 className="text-lg font-extrabold tracking-wide text-white flex items-center gap-2">
              <Activity className="w-5 h-5 text-cyan-400" />
              NIFTY INTRADAY AUTO-TRADING STRATEGIES
            </h2>
            <span className="px-2 py-0.5 rounded text-[10px] font-bold bg-cyan-500/20 text-cyan-300 border border-cyan-500/30">
              v1.0 • {(status?.market_data?.provider || "BROKER").toUpperCase()} {status?.market_data?.provider_active ? "CONNECTED" : "INACTIVE"}
            </span>
          </div>
          <p className="text-xs text-slate-400 mt-0.5">
            Platform mode is separate from per-strategy execution authority. Status refreshes every 1.5s; charts refresh candles every 3s and live quotes every 1s.
          </p>
          <div className="flex flex-wrap items-center gap-2 mt-2 text-[10px] font-bold tracking-wider">
            <span className={`px-2 py-1 rounded border ${status?.scheduler?.running ? "bg-emerald-500/15 text-emerald-300 border-emerald-500/30" : "bg-rose-500/15 text-rose-300 border-rose-500/30"}`}>
              SCHEDULER {status?.scheduler?.running ? "RUNNING" : "STOPPED"} · {status?.scheduler?.evaluation_interval_seconds ?? "--"}s
            </span>
            <span className="px-2 py-1 rounded bg-cyan-500/15 text-cyan-300 border border-cyan-500/30">
              A · {status?.strategies?.trend_pullback?.effective_call_mode ?? "--"}/{status?.strategies?.trend_pullback?.effective_put_mode ?? "--"}
            </span>
            <span className="px-2 py-1 rounded bg-amber-500/15 text-amber-300 border border-amber-500/30">
              B · {status?.strategies?.volatility_breakout?.effective_call_mode ?? "--"}
            </span>
            <span className="px-2 py-1 rounded bg-violet-500/15 text-violet-300 border border-violet-500/30">
              C · {status?.strategies?.di_continuation?.promotion_state ?? "LOCKED"}
            </span>
            <span className="px-2 py-1 rounded bg-fuchsia-500/15 text-fuchsia-300 border border-fuchsia-500/30">
              D · {status?.strategies?.sr_momentum_breakout?.promotion_state ?? "LOCKED"}
            </span>
          </div>
        </div>

        {/* Master Action Buttons */}
        <div className="flex flex-wrap items-center gap-2.5">
          {/* Platform mode request — never overrides per-strategy policy */}
          <div className="flex items-center gap-1.5">
            <span className="text-[9px] uppercase tracking-wider text-slate-500">Platform</span>
            <div className="bg-slate-900 border border-slate-800 rounded-lg p-0.5 flex items-center">
            <button
              onClick={() => handleModeChange("PAPER")}
              disabled={actionLoading}
              className={`px-3 py-1 rounded text-xs font-semibold transition-all ${
                status?.config.mode === "PAPER"
                  ? "bg-blue-600 text-white shadow"
                  : "text-slate-400 hover:text-slate-200"
              }`}
            >
              PAPER
            </button>
            <button
              onClick={() => handleModeChange("LIVE")}
              disabled={actionLoading}
              className={`px-3 py-1 rounded text-xs font-semibold transition-all ${
                status?.config.mode === "LIVE"
                  ? "bg-rose-600 text-white shadow"
                  : "text-slate-400 hover:text-slate-200"
              }`}
            >
              LIVE
            </button>
            </div>
          </div>

          {/* Arm System */}
          <button
            onClick={handleArmToggle}
            disabled={
              actionLoading ||
              status?.config.kill_switch ||
              (status?.config.mode !== "LIVE" && !status?.config.system_armed) ||
              (status?.config.mode === "LIVE" &&
                !status?.config.system_armed &&
                !hasLiveEligibleStrategy)
            }
            className={`px-3.5 py-1.5 rounded-lg text-xs font-bold flex items-center gap-1.5 transition-all ${
              status?.config.system_armed
                ? "bg-rose-600 hover:bg-rose-500 text-white shadow-lg shadow-rose-900/30 animate-pulse"
                : "bg-slate-800 hover:bg-slate-700 text-slate-300 border border-slate-700"
            }`}
          >
            {status?.config.system_armed ? (
              <>
                <ShieldAlert className="w-4 h-4" />
                SYSTEM ARMED
              </>
            ) : (
              <>
                <ShieldCheck className="w-4 h-4 text-emerald-400" />
                {status?.config.mode === "LIVE"
                  ? hasLiveEligibleStrategy
                    ? "ARM PROMOTED STRATEGIES"
                    : "NO STRATEGY PROMOTED"
                  : "LIVE ARM N/A"}
              </>
            )}
          </button>

          {/* Auto Trade Toggle */}
          <button
            onClick={handleAutoTradeToggle}
            disabled={actionLoading || status?.config.kill_switch}
            className={`px-3.5 py-1.5 rounded-lg text-xs font-bold flex items-center gap-1.5 transition-all ${
              status?.config.auto_trade_enabled
                ? "bg-emerald-600 hover:bg-emerald-500 text-white"
                : "bg-slate-800 hover:bg-slate-700 text-slate-400"
            }`}
          >
            <Zap className="w-4 h-4" />
            {status?.config.auto_trade_enabled ? "AUTO: ON" : "AUTO: OFF"}
          </button>

          {/* Evaluate Now */}
          <button
            onClick={handleEvaluateNow}
            disabled={isEvaluating}
            className="px-3.5 py-1.5 bg-slate-800 hover:bg-slate-700 text-cyan-300 rounded-lg text-xs font-semibold border border-slate-700 flex items-center gap-1.5 transition-colors"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${isEvaluating ? "animate-spin" : ""}`} />
            Evaluate Now
          </button>

          {/* Kill Switch */}
          <button
            onClick={handleKillSwitch}
            disabled={actionLoading}
            className={`px-3.5 py-1.5 rounded-lg text-xs font-bold flex items-center gap-1.5 transition-all ${
              status?.config.kill_switch
                ? "bg-red-700 text-white ring-2 ring-red-400 animate-bounce"
                : "bg-red-950/60 hover:bg-red-900/80 text-red-300 border border-red-800/80"
            }`}
          >
            <AlertOctagon className="w-4 h-4 text-rose-400" />
            {status?.config.kill_switch ? "KILL SWITCH ACTIVE" : "KILL SWITCH"}
          </button>
        </div>
      </div>

      {/* Tabs Navigation */}
      <div className="bg-slate-950/80 border-b border-slate-800/80 px-6 pt-2 flex items-center gap-6">
        <button
          onClick={() => setActiveTab("overview")}
          className={`pb-3 text-xs font-bold uppercase tracking-wider flex items-center gap-2 border-b-2 transition-all ${
            activeTab === "overview"
              ? "border-cyan-400 text-cyan-400"
              : "border-transparent text-slate-400 hover:text-slate-200"
          }`}
        >
          <Activity className="w-4 h-4" />
          1. Live Monitor & Strategies
        </button>

        <button
          onClick={() => setActiveTab("parameters")}
          className={`pb-3 text-xs font-bold uppercase tracking-wider flex items-center gap-2 border-b-2 transition-all ${
            activeTab === "parameters"
              ? "border-cyan-400 text-cyan-400"
              : "border-transparent text-slate-400 hover:text-slate-200"
          }`}
        >
          <Sliders className="w-4 h-4" />
          2. Parameters & Risk Controls
        </button>

        <button
          onClick={() => setActiveTab("decision_log")}
          className={`pb-3 text-xs font-bold uppercase tracking-wider flex items-center gap-2 border-b-2 transition-all ${
            activeTab === "decision_log"
              ? "border-cyan-400 text-cyan-400"
              : "border-transparent text-slate-400 hover:text-slate-200"
          }`}
        >
          <FileText className="w-4 h-4" />
          3. Decision Audit Log
        </button>

        <button
          onClick={() => setActiveTab("history")}
          className={`pb-3 text-xs font-bold uppercase tracking-wider flex items-center gap-2 border-b-2 transition-all ${
            activeTab === "history"
              ? "border-cyan-400 text-cyan-400"
              : "border-transparent text-slate-400 hover:text-slate-200"
          }`}
        >
          <History className="w-4 h-4" />
          4. Performance & History
        </button>

        <button
          onClick={() => setActiveTab("simulation")}
          className={`pb-3 text-xs font-bold uppercase tracking-wider flex items-center gap-2 border-b-2 transition-all ${
            activeTab === "simulation"
              ? "border-indigo-400 text-indigo-400"
              : "border-transparent text-slate-400 hover:text-slate-200"
          }`}
        >
          <Play className="w-4 h-4 fill-indigo-400" />
          5. Day Replay & Simulation
        </button>
      </div>

      {/* Main Tab Content */}
      <div className="p-6">
        {activeTab === "overview" && (
          <div className="space-y-6">
            <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
              <div className="h-[300px] rounded-xl overflow-hidden border border-slate-800 bg-slate-950">
                <div className="px-3 py-1.5 text-[10px] uppercase tracking-wider font-bold text-cyan-300 border-b border-slate-800">
                  Spot feed · Strategy B / D
                </div>
                <div className="h-[270px]">
                  <TradingChart instrumentId="INST-NIFTY-INDEX" symbol="NIFTY 50 SPOT" fixedInterval="5m" compact showIntervalSelector={false} />
                </div>
              </div>
              <div className="h-[300px] rounded-xl overflow-hidden border border-slate-800 bg-slate-950">
                <div className="px-3 py-1.5 text-[10px] uppercase tracking-wider font-bold text-indigo-300 border-b border-slate-800">
                  Futures feed · Strategy A / C
                </div>
                <div className="h-[270px]">
                  {status?.market_data?.futures_instrument ? (
                    <TradingChart instrumentId={status.market_data.futures_instrument} symbol="NIFTY ACTIVE FUTURES" fixedInterval="5m" compact showIntervalSelector={false} />
                  ) : (
                    <div className="h-full flex items-center justify-center text-xs text-slate-500">Waiting for active NIFTY futures contract...</div>
                  )}
                </div>
              </div>
            </div>
            <TabOverview status={status} onRefresh={loadStatus} />
          </div>
        )}
        {activeTab === "parameters" && (
          <TabParameters status={status} onRefresh={loadStatus} />
        )}
        {activeTab === "decision_log" && <TabDecisionLog />}
        {activeTab === "history" && <TabHistory />}
        {activeTab === "simulation" && (
          <TabReplaySimulation
            historicalSource={status?.market_data?.provider === "kite" ? "KITE" : "BREEZE"}
          />
        )}
      </div>
    </div>
  );
};
