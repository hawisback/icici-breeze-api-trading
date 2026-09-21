"use client";

import React, { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
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

export const StrategyDashboard: React.FC = () => {
  const [activeTab, setActiveTab] = useState<"overview" | "parameters" | "decision_log" | "history" | "simulation">("overview");
  const queryClient = useQueryClient();
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
  });

  const handleArmToggle = async () => {
    if (!status) return;
    const targetArmed = !status.config.system_armed;
    if (targetArmed && status.config.mode === "LIVE") {
      const broker = status.market_data?.provider === "kite" ? "Zerodha Kite" : status.market_data?.provider === "breeze" ? "ICICI Breeze" : "the configured live broker";
      if (!confirm(`WARNING: Arming the system in LIVE mode allows real ${broker} order routing. Are you sure?`)) {
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
      if (!confirm("Switching to LIVE mode. Ensure live session token and credentials are valid.")) return;
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
            Strategy A (Trend Pullback) & Strategy B (Volatility Breakout) with automated contract selection under ₹{status?.config.option_selection.max_option_premium ?? 70} cap.
          </p>
          <div className="flex items-center gap-2 mt-2 text-[10px] font-bold tracking-wider">
            <span className="px-2 py-1 rounded bg-amber-500/15 text-amber-300 border border-amber-500/30">PUT — PAPER</span>
            <span className="px-2 py-1 rounded bg-indigo-500/15 text-indigo-300 border border-indigo-500/30">CALL — SHADOW ONLY</span>
            <span className="px-2 py-1 rounded bg-rose-500/15 text-rose-300 border border-rose-500/30">LIVE TRADING — DISABLED</span>
          </div>
        </div>

        {/* Master Action Buttons */}
        <div className="flex flex-wrap items-center gap-2.5">
          {/* Mode Selector */}
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

          {/* Arm System */}
          <button
            onClick={handleArmToggle}
            disabled={actionLoading || status?.config.kill_switch}
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
                ARM SYSTEM
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
          <TabOverview status={status} onRefresh={loadStatus} />
        )}
        {activeTab === "parameters" && (
          <TabParameters status={status} onRefresh={loadStatus} />
        )}
        {activeTab === "decision_log" && <TabDecisionLog />}
        {activeTab === "history" && <TabHistory />}
        {activeTab === "simulation" && <TabReplaySimulation />}
      </div>
    </div>
  );
};
