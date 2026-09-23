"use client";

import React, { useState } from "react";
import {
  Activity,
  AlertCircle,
  AlertTriangle,
  ArrowDownRight,
  ArrowUpRight,
  CheckCircle2,
  Clock,
  ExternalLink,
  Flame,
  Layers,
  Play,
  RefreshCw,
  Shield,
  ShieldAlert,
  ShieldCheck,
  TrendingDown,
  TrendingUp,
  XCircle,
  Zap,
  Sliders,
} from "lucide-react";
import { AutoTradeData, StrategyStatusData, exitStrategyTrade } from "../../lib/api";
import { TriggerWatchWidget } from "./TriggerWatchWidget";
import { OverrideModal } from "./OverrideModal";

interface TabOverviewProps {
  status: StrategyStatusData | null;
  onRefresh: () => void;
}

export const TabOverview: React.FC<TabOverviewProps> = ({ status, onRefresh }) => {
  const [exitingTradeId, setExitingTradeId] = useState<string | null>(null);
  const [isOverrideModalOpen, setIsOverrideModalOpen] = useState<boolean>(false);

  if (!status) {
    return (
      <div className="flex items-center justify-center p-12 text-slate-400">
        <RefreshCw className="w-5 h-5 animate-spin mr-2" />
        Loading auto-trading engine state...
      </div>
    );
  }

  const { config, features, active_trades, strategies, in_trading_window } = status;
  const activeTrade = active_trades && active_trades.length > 0 ? active_trades[0] : null;
  const fleet = [
    ["A", strategies.trend_pullback],
    ["B", strategies.volatility_breakout],
    ["C", strategies.di_continuation],
    ["D", strategies.sr_momentum_breakout],
  ] as const;

  const handleManualExit = async (tradeId: string) => {
    if (!confirm("Are you sure you want to immediately exit this auto-trade position?")) return;
    try {
      setExitingTradeId(tradeId);
      await exitStrategyTrade(tradeId, "MANUAL_UI_EXIT");
      onRefresh();
    } catch (e: any) {
      alert(`Exit failed: ${e.message}`);
    } finally {
      setExitingTradeId(null);
    }
  };

  // Trailing stop steps
  const rMultiple = activeTrade ? activeTrade.current_r : 0;
  const peakR = activeTrade ? activeTrade.peak_r : 0;

  return (
    <div className="space-y-6">
      {/* 1. Global Session & Safeguard Status Bar */}
      <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-6 gap-3">
        <div className="bg-slate-900/90 border border-slate-800 rounded-lg p-3">
          <div className="text-[11px] text-slate-400 uppercase tracking-wider mb-1 flex items-center gap-1.5">
            <Clock className="w-3.5 h-3.5 text-blue-400" />
            Trading Window
          </div>
          <div className="flex items-center gap-2">
            <span
              className={`w-2 h-2 rounded-full ${
                in_trading_window ? "bg-emerald-400 animate-pulse" : "bg-amber-400"
              }`}
            />
            <span className="text-sm font-semibold text-slate-200">
              {in_trading_window ? `ACTIVE (A: ${config.tunables.entry_session_start}–${config.tunables.entry_session_end})` : "CLOSED / NO NEW A ENTRY"}
            </span>
          </div>
        </div>

        <div className="bg-slate-900/90 border border-slate-800 rounded-lg p-3">
          <div className="text-[11px] text-slate-400 uppercase tracking-wider mb-1 flex items-center gap-1.5">
            <Shield className="w-3.5 h-3.5 text-indigo-400" />
            Trading Mode
          </div>
          <div className="text-sm font-bold tracking-wide">
            <span
              className={`px-2 py-0.5 rounded text-xs ${
                config.mode === "LIVE"
                  ? "bg-rose-500/20 text-rose-300 border border-rose-500/30"
                  : "bg-blue-500/20 text-blue-300 border border-blue-500/30"
              }`}
            >
              {config.mode}
            </span>
          </div>
        </div>

        <div className="bg-slate-900/90 border border-slate-800 rounded-lg p-3">
          <div className="text-[11px] text-slate-400 uppercase tracking-wider mb-1 flex items-center gap-1.5">
            <ShieldCheck className="w-3.5 h-3.5 text-emerald-400" />
            Live Authorization
          </div>
          <div className="text-sm font-semibold">
            {config.system_armed ? (
              <span className="text-rose-400 font-bold flex items-center gap-1">
                <span className="w-2 h-2 rounded-full bg-rose-500 animate-ping" />
                ARMED FOR LIVE
              </span>
            ) : (
              <span className="text-slate-400">DISARMED (SAFE)</span>
            )}
          </div>
        </div>

        <div className="bg-slate-900/90 border border-slate-800 rounded-lg p-3">
          <div className="text-[11px] text-slate-400 uppercase tracking-wider mb-1 flex items-center gap-1.5">
            <Zap className="w-3.5 h-3.5 text-amber-400" />
            Auto Execution
          </div>
          <div className="text-sm font-semibold">
            {config.auto_trade_enabled ? (
              <span className="text-emerald-400">ENABLED</span>
            ) : (
              <span className="text-slate-500">PAUSED</span>
            )}
          </div>
        </div>

        <div
          onClick={() => setIsOverrideModalOpen(true)}
          className="bg-slate-900/90 border border-slate-800 hover:border-cyan-500/50 cursor-pointer rounded-lg p-3 transition group"
        >
          <div className="text-[11px] text-slate-400 uppercase tracking-wider mb-1 flex items-center justify-between">
            <span className="flex items-center gap-1.5">
              <Layers className="w-3.5 h-3.5 text-cyan-400" />
              Strategy B Cap
            </span>
            <span className="text-[9px] text-cyan-400 opacity-0 group-hover:opacity-100 transition">Tune ⚡</span>
          </div>
          <div className="text-sm font-bold text-cyan-300 flex items-center justify-between">
            <span>₹{(status.active_overrides?.max_option_premium_cap || config.option_selection.max_option_premium).toFixed(0)}</span>
            {status.active_overrides?.max_option_premium_cap && (
              <span className="text-[9px] px-1 py-0.2 bg-amber-500/20 text-amber-300 rounded font-normal">custom</span>
            )}
          </div>
        </div>

        <div className="bg-slate-900/90 border border-slate-800 rounded-lg p-3">
          <div className="text-[11px] text-slate-400 uppercase tracking-wider mb-1 flex items-center gap-1.5">
            <ShieldAlert className="w-3.5 h-3.5 text-rose-400" />
            Option Hard SL
          </div>
          <div className="text-sm font-bold text-rose-400">
            -{config.risk.option_hard_stop_pct}%
          </div>
        </div>
      </div>

      {/* 2. Active Trade Card with Multi-Level Trailing Stop Visualizer */}
      <div className="bg-slate-900/90 border border-slate-800 rounded-xl overflow-hidden shadow-lg">
        <div className="bg-slate-950 px-5 py-3.5 border-b border-slate-800 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-2.5 h-2.5 rounded-full bg-emerald-500 animate-pulse" />
            <h3 className="text-sm font-bold tracking-wider text-slate-200 uppercase flex items-center gap-2">
              <Flame className="w-4 h-4 text-amber-400" />
              Active Trade & Trailing Stop Engine
            </h3>
            {activeTrade && (
              <span className="px-2.5 py-0.5 rounded-full text-xs font-semibold bg-emerald-500/20 text-emerald-300 border border-emerald-500/30">
                {activeTrade.strategy} • {activeTrade.direction}
              </span>
            )}
          </div>
          {activeTrade && (
            <button
              onClick={() => handleManualExit(activeTrade.trade_id)}
              disabled={exitingTradeId === activeTrade.trade_id}
              className="px-3 py-1 bg-rose-600 hover:bg-rose-500 text-white rounded text-xs font-semibold flex items-center gap-1.5 transition-colors disabled:opacity-50"
            >
              <XCircle className="w-3.5 h-3.5" />
              {exitingTradeId === activeTrade.trade_id ? "EXITING..." : "MANUAL EXIT"}
            </button>
          )}
        </div>

        <div className="p-5">
          {activeTrade ? (
            <div className="space-y-6">
              {/* Summary Numbers */}
              <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-7 gap-4">
                <div>
                  <div className="text-[11px] text-slate-400">CONTRACT</div>
                  <div className="text-base font-bold text-slate-100">{activeTrade.contract_symbol}</div>
                  <div className="text-xs text-slate-400">Expiry {activeTrade.expiry} • Strike {activeTrade.strike}</div>
                  <div className="text-xs text-slate-400">{activeTrade.lots} Lots ({activeTrade.quantity} Qty)</div>
                </div>

                <div>
                  <div className="text-[11px] text-slate-400">ENTRY PREMIUM</div>
                  <div className="text-base font-bold text-slate-200">₹{activeTrade.entry_option_price.toFixed(2)}</div>
                  <div className="text-xs text-slate-400">{activeTrade.strategy === "TREND_PULLBACK" ? "Futures" : "Spot"}: {(activeTrade.underlying_entry_price ?? activeTrade.entry_spot_price).toFixed(1)}</div>
                </div>

                <div>
                  <div className="text-[11px] text-slate-400">CURRENT PREMIUM</div>
                  <div className="text-base font-bold text-cyan-300">₹{activeTrade.current_option_price.toFixed(2)}</div>
                  <div className="text-xs text-slate-400">Bid {activeTrade.current_bid?.toFixed(2) ?? "--"} / Ask {activeTrade.current_ask?.toFixed(2) ?? "--"}</div>
                  <div className="text-xs text-slate-400">LTP {activeTrade.current_ltp?.toFixed(2) ?? "--"} • {activeTrade.strategy === "TREND_PULLBACK" ? "Futures" : "Spot"}: {(activeTrade.underlying_current_price ?? activeTrade.current_spot_price).toFixed(1)}</div>
                </div>

                <div>
                  <div className="text-[11px] text-slate-400">UNREALIZED PnL</div>
                  <div
                    className={`text-base font-extrabold ${
                      activeTrade.unrealized_pnl >= 0 ? "text-emerald-400" : "text-rose-400"
                    }`}
                  >
                    {activeTrade.unrealized_pnl >= 0 ? "+" : ""}₹{activeTrade.unrealized_pnl.toFixed(2)}
                  </div>
                  <div className="text-xs text-slate-400">
                    {activeTrade.current_r >= 0 ? "+" : ""}{activeTrade.current_r}R (Peak: +{activeTrade.peak_r}R)
                  </div>
                </div>

                <div>
                  <div className="text-[11px] text-slate-400">STRUCTURAL UNDERLYING STOP</div>
                  <div className="text-base font-bold text-amber-300">
                    {activeTrade.current_trailing_stop.toFixed(1)}
                  </div>
                  <div className="text-xs text-slate-400">R-Risk: {activeTrade.initial_r_points.toFixed(1)} pts</div>
                </div>

                <div>
                  <div className="text-[11px] text-slate-400">OPTION HARD STOP (-25%)</div>
                  <div className="text-base font-bold text-rose-400">
                    ₹{activeTrade.option_hard_stop_price.toFixed(2)}
                  </div>
                  <div className="text-xs text-slate-400">Emergency cut</div>
                </div>

                <div>
                  <div className="text-[11px] text-slate-400">REVERSAL HEALTH SCORE</div>
                  <div className="flex items-center gap-2 mt-0.5">
                    <span
                      className={`px-2 py-0.5 rounded text-xs font-bold ${
                        activeTrade.reversal_score <= 1
                          ? "bg-emerald-500/20 text-emerald-300 border border-emerald-500/30"
                          : activeTrade.reversal_score === 2
                          ? "bg-amber-500/20 text-amber-300 border border-amber-500/30"
                          : "bg-rose-500/20 text-rose-300 border border-rose-500/30 animate-pulse"
                      }`}
                    >
                      {activeTrade.reversal_score} / 10
                    </span>
                    <span className="text-xs text-slate-400">
                      {activeTrade.reversal_score <= 1
                        ? "HEALTHY"
                        : activeTrade.reversal_score === 2
                        ? "CAUTION"
                        : "EXIT THESIS"}
                    </span>
                  </div>
                </div>
              </div>

              {activeTrade.strategy === "TREND_PULLBACK" && (
                <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-xs">
                  <div className="bg-slate-950/70 border border-slate-800 rounded p-3"><div className="text-[10px] uppercase text-slate-500">Futures Contract</div><div className="font-mono text-cyan-300 truncate">{activeTrade.futures_contract_id || "Not recorded"}</div></div>
                  <div className="bg-slate-950/70 border border-slate-800 rounded p-3"><div className="text-[10px] uppercase text-slate-500">Underlying R</div><div className="font-bold text-slate-200">{(activeTrade.underlying_r ?? activeTrade.current_r).toFixed(2)}R</div></div>
                  <div className="bg-slate-950/70 border border-slate-800 rounded p-3"><div className="text-[10px] uppercase text-slate-500">T1 / Remaining</div><div className="font-bold text-slate-200">{activeTrade.t1_exit_pending ? "T1 EXIT PENDING" : activeTrade.t1_reached ? "T1 REACHED" : "T1 PENDING"} · {activeTrade.remaining_quantity ?? activeTrade.quantity} qty</div></div>
                  <div className="bg-slate-950/70 border border-slate-800 rounded p-3"><div className="text-[10px] uppercase text-slate-500">Option Data / Exit</div><div className={`font-bold ${activeTrade.pending_exit_reason || activeTrade.option_data_status === "DEGRADED" ? "text-amber-300" : "text-emerald-300"}`}>{activeTrade.pending_exit_reason || activeTrade.option_data_status || "OK"}</div></div>
                </div>
              )}
                            {/* Multi-Level Trailing Stop Visual Progress Bar */}
              <div className="bg-slate-950/70 border border-slate-800/80 rounded-lg p-4">
                <div className="text-xs font-semibold text-slate-300 mb-3 flex items-center justify-between">
                  <span>STRATEGY A R LIFECYCLE (+1R trail activation → +1.5R T1 → +2.5R runner reference)</span>
                  <span className="text-cyan-400">Lifecycle State: {activeTrade.state}</span>
                </div>

                <div className="grid grid-cols-4 gap-2 text-center text-xs">
                  {/* Step 1 */}
                  <div
                    className={`p-2.5 rounded border transition-all ${
                      rMultiple < 1.0
                        ? "bg-blue-950/40 border-blue-500/50 text-blue-200"
                        : "bg-emerald-950/40 border-emerald-500/40 text-emerald-300"
                    }`}
                  >
                    <div className="font-bold">Initial Risk (&lt; +1.0R)</div>
                    <div className="text-[10px] text-slate-400 mt-1">Structural stop: {activeTrade.initial_structural_stop.toFixed(1)}</div>
                    <div className="text-[10px] text-slate-400">Full trade risk active</div>
                  </div>

                  {/* Step 2 */}
                  <div
                    className={`p-2.5 rounded border transition-all ${
                      rMultiple >= 1.0
                        ? "bg-emerald-950/60 border-emerald-400 text-emerald-200 shadow-sm"
                        : "bg-slate-900 border-slate-800 text-slate-500"
                    }`}
                  >
                    <div className="font-bold">+1.0R Breakeven</div>
                    <div className="text-[10px] mt-1">
                      {rMultiple >= 1.0 ? "Stop moved to Entry + Cost Buffer" : "Awaiting +1.0R move"}
                    </div>
                    <div className="text-[10px]">Zero downside risk</div>
                  </div>

                  {/* Step 3 */}
                  <div
                    className={`p-2.5 rounded border transition-all ${
                      rMultiple >= 1.5
                        ? "bg-emerald-950/60 border-emerald-400 text-emerald-200 shadow-sm"
                        : "bg-slate-900 border-slate-800 text-slate-500"
                    }`}
                  >
                    <div className="font-bold">+1.5R T1 Partial Exit</div>
                    <div className="text-[10px] mt-1">
                      {activeTrade.t1_reached ? `T1 reached · ${activeTrade.t1_exit_quantity ?? 0} qty target` : "Awaiting +1.5R T1"}
                    </div>
                    <div className="text-[10px]">Partial realization; runner remains managed</div>
                  </div>

                  {/* Step 4 */}
                  <div
                    className={`p-2.5 rounded border transition-all ${
                      rMultiple >= 2.5
                        ? "bg-cyan-950/60 border-cyan-400 text-cyan-200 shadow-sm"
                        : "bg-slate-900 border-slate-800 text-slate-500"
                    }`}
                  >
                    <div className="font-bold">+2.5R Runner Reference</div>
                    <div className="text-[10px] mt-1">
                      {rMultiple >= 2.5 ? "Runner beyond reference; +1R trailing remains active" : "Runner managed after T1; reference +2.5R"}
                    </div>
                    <div className="text-[10px]">Riding trend runner</div>
                  </div>
                </div>
              </div>
            </div>
          ) : (
            <div className="text-center py-6 text-slate-400">
              <CheckCircle2 className="w-8 h-8 text-slate-600 mx-auto mb-2" />
              <div className="text-sm font-semibold text-slate-300">No Open Positions Currently</div>
              <div className="text-xs text-slate-500 max-w-md mx-auto mt-1">
                Strategy A evaluates completed 15-minute NIFTY futures bars; Strategy B keeps its existing data path.
              </div>
            </div>
          )}
        </div>
      </div>

      {/* 2.5 Live Trigger Radar: What Is The Trade Waiting For? */}
      <TriggerWatchWidget
        diagnostics={status.trigger_diagnostics}
        onRefresh={onRefresh}
        defaultCap={config.option_selection.max_option_premium}
        marketData={status.market_data}
        entryWindowStart={config.tunables.entry_session_start}
        entryWindowEnd={config.tunables.entry_session_end}
      />

      {/* 3. Market Regime & Derivatives Flow Vector */}
      <div className="bg-slate-900/90 border border-slate-800 rounded-xl p-5 shadow-lg">
        <h3 className="text-sm font-bold tracking-wider text-slate-200 uppercase mb-4 flex items-center gap-2">
          <Activity className="w-4 h-4 text-cyan-400" />
          Technical & Derivatives Feature Vector (NIFTY 50)
        </h3>

        <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-4">
          <div className="bg-slate-950/80 border border-slate-800/80 p-3 rounded-lg">
            <div className="text-[11px] text-slate-400">NIFTY SPOT</div>
            <div className="text-base font-extrabold text-slate-100">{features.spot_price.toFixed(2)}</div>
            <div className="text-xs text-emerald-400 flex items-center gap-0.5">
              Regime: {features.trend_regime}
            </div>
          </div>

          <div className="bg-slate-950/80 border border-slate-800/80 p-3 rounded-lg">
            <div className="text-[11px] text-slate-400">15m EMAs (9 / 20 / 50)</div>
            <div className="text-xs font-semibold text-slate-300">
              {features.ema9_15m.toFixed(0)} / {features.ema20_15m.toFixed(0)} / {features.ema50_15m.toFixed(0)}
            </div>
            <div className="text-xs text-slate-400">Slope: {features.ema20_slope_15m}</div>
          </div>

          <div className="bg-slate-950/80 border border-slate-800/80 p-3 rounded-lg">
            <div className="text-[11px] text-slate-400">15m ADX & DI</div>
            <div className="text-xs font-semibold text-slate-200">
              ADX: {features.adx_15m}
            </div>
            <div className="text-xs text-slate-400">
              +DI: {features.plus_di_15m} | -DI: {features.minus_di_15m}
            </div>
          </div>

          <div className="bg-slate-950/80 border border-slate-800/80 p-3 rounded-lg">
            <div className="text-[11px] text-slate-400">5m ATR & BBWIDTH</div>
            <div className="text-xs font-semibold text-slate-200">
              ATR(14): {features.atr_5m.toFixed(1)} pts
            </div>
            <div className="text-xs text-slate-400">
              BBWidth Percentile: {features.bb_width_percentile}%
            </div>
          </div>

          <div className="bg-slate-950/80 border border-slate-800/80 p-3 rounded-lg">
            <div className="text-[11px] text-slate-400">FUTURES & VWAP</div>
            <div className="text-xs font-semibold text-slate-200">
              VWAP: {features.futures_vwap.toFixed(1)} (RVOL: {features.rvol_5m})
            </div>
            <div className="text-xs text-cyan-400">{features.futures_buildup}</div>
          </div>

          <div className="bg-slate-950/80 border border-slate-800/80 p-3 rounded-lg">
            <div className="text-[11px] text-slate-400">DERIVATIVES SCORES</div>
            <div className="text-xs font-bold text-emerald-400">
              Bull Score: +{features.bull_derivatives_score.toFixed(1)} / 5.0
            </div>
            <div className="text-xs font-bold text-rose-400">
              Bear Score: +{features.bear_derivatives_score.toFixed(1)} / 5.0
            </div>
          </div>
        </div>
      </div>

      {/* 3.5 Four-Strategy Fleet Runtime */}
      <div className="bg-slate-900/90 border border-slate-800 rounded-xl p-5 shadow-lg">
        <div className="flex items-center justify-between gap-3 mb-4">
          <div>
            <h3 className="text-sm font-bold tracking-wider text-slate-200 uppercase">Strategy Fleet · Runtime & Stops</h3>
            <p className="text-[11px] text-slate-500 mt-1">A/B use the production lifecycle. C/D are frozen paper candidates and cannot route live until explicitly promoted.</p>
          </div>
          <span className="text-[10px] font-mono text-cyan-300 bg-cyan-500/10 border border-cyan-500/20 rounded px-2 py-1">AUTO REFRESH 1.5s</span>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-3">
          {fleet.map(([shortName, item]) => (
            <div key={shortName} className="bg-slate-950/70 border border-slate-800 rounded-lg p-3 space-y-2">
              <div className="flex items-start justify-between gap-2">
                <div>
                  <div className="text-[10px] uppercase tracking-wider text-slate-500">Strategy {shortName}</div>
                  <div className="text-xs font-bold text-slate-200">{item.label || item.state}</div>
                </div>
                <span className={`px-2 py-0.5 rounded text-[9px] font-bold border ${item.active_trade_id ? "bg-emerald-500/15 text-emerald-300 border-emerald-500/30" : "bg-slate-800 text-slate-400 border-slate-700"}`}>
                  {item.active_trade_id ? "ACTIVE" : item.state}
                </span>
              </div>
              <div className="grid grid-cols-2 gap-2 text-[10px]">
                <div className="bg-slate-900 rounded p-2"><div className="text-slate-500">Mode</div><div className="font-bold text-blue-300">{item.execution_mode || "N/A"}</div></div>
                <div className="bg-slate-900 rounded p-2"><div className="text-slate-500">Live routing</div><div className={`font-bold ${item.live_trading_allowed ? "text-emerald-300" : "text-amber-300"}`}>{item.live_trading_allowed ? "ELIGIBLE" : "LOCKED"}</div></div>
                <div className="bg-slate-900 rounded p-2"><div className="text-slate-500">Current R</div><div className="font-mono font-bold text-slate-200">{item.current_r == null ? "--" : `${item.current_r.toFixed(2)}R`}</div></div>
                <div className="bg-slate-900 rounded p-2"><div className="text-slate-500">Trailing / protective SL</div><div className="font-mono font-bold text-rose-300">{item.current_trailing_stop == null ? "--" : `₹${item.current_trailing_stop.toFixed(2)}`}</div></div>
              </div>
              {item.paper_closed_trades != null && (
                <div className="flex justify-between text-[10px] text-slate-400 border-t border-slate-800 pt-2">
                  <span>Paper {item.paper_open_trades || 0} open / {item.paper_closed_trades || 0} closed</span>
                  <span className={(item.paper_net_pnl || 0) >= 0 ? "text-emerald-300" : "text-rose-300"}>₹{(item.paper_net_pnl || 0).toFixed(2)}</span>
                </div>
              )}
              {item.candidate_spec_fingerprint && (
                <div className="text-[9px] text-slate-600 font-mono truncate" title={item.candidate_spec_fingerprint}>spec {item.candidate_spec_fingerprint.slice(0, 12)}…</div>
              )}
            </div>
          ))}
        </div>
      </div>
      {/* 4. Strategy A & Strategy B Live Setup Condition Cards */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
        {/* Strategy A V3 Card */}
        <div className="bg-slate-900/90 border border-cyan-900/60 rounded-xl p-5 shadow-lg">
          <div className="flex items-center justify-between pb-3 border-b border-slate-800 mb-4">
            <div>
              <div className="text-xs font-bold text-cyan-400 uppercase tracking-wider">Strategy A V3 (Primary)</div>
              <h4 className="text-base font-bold text-slate-100">NIFTY Futures Trend-Pullback Momentum</h4>
            </div>
            <div className="text-right">
              <span className={`px-2.5 py-1 rounded text-xs font-bold ${
                strategies.trend_pullback.state === "TRIGGERED"
                  ? "bg-emerald-500/20 text-emerald-300 border border-emerald-500/30 animate-pulse"
                  : "bg-slate-800 text-slate-300"
              }`}>
                {strategies.trend_pullback.state}
              </span>
            </div>
          </div>

          <p className="text-xs text-slate-400 mb-4">
            Signal and structure are driven only by completed 15-minute NIFTY futures bars. V3 has no hard ADX floor; trend quality is gated by EMA/DI alignment plus momentum-health decay and directional EMA20 slope.
          </p>

          <div className="space-y-2 text-xs">
            <div className="flex items-center justify-between p-2 bg-slate-950/60 rounded border border-slate-800/60">
              <span className="text-slate-300">Trend structure</span>
              <span className="text-cyan-300 font-bold">EMA20/50 + DI · sep ≥ {config.tunables.ema_separation_min_atr.toFixed(2)} ATR</span>
            </div>
            <div className="flex items-center justify-between p-2 bg-slate-950/60 rounded border border-slate-800/60">
              <span className="text-slate-300">V3 momentum health</span>
              <span className="text-cyan-300 font-bold">
                ADX Δ2 ≥ {config.tunables.momentum_adx_min_delta_2bars.toFixed(1)} · EMA20 slope [{config.tunables.momentum_ema20_slope_min_atr.toFixed(2)}, {config.tunables.momentum_ema20_slope_max_atr.toFixed(2)}) ATR
              </span>
            </div>
            <div className="flex items-center justify-between p-2 bg-slate-950/60 rounded border border-slate-800/60">
              <span className="text-slate-300">Pullback confluence</span>
              <span className="text-cyan-300 font-bold">S/R zone {config.tunables.sr_zone_atr.toFixed(2)} ATR · EMA/VWAP {config.tunables.confluence_distance_atr.toFixed(2)} ATR</span>
            </div>
            <div className="flex items-center justify-between p-2 bg-slate-950/60 rounded border border-slate-800/60">
              <span className="text-slate-300">Confirmation candle</span>
              <span className="text-cyan-300 font-bold">Body ≥ {config.tunables.confirmation_min_body_ratio.toFixed(2)} · close ≤ {(config.tunables.confirmation_close_location_pct * 100).toFixed(0)}% · range ≤ {config.tunables.confirmation_max_range_atr.toFixed(2)} ATR</span>
            </div>
            <div className="flex items-center justify-between p-2 bg-slate-950/60 rounded border border-slate-800/60">
              <span className="text-slate-300">Structural risk</span>
              <span className="text-cyan-300 font-bold">{config.tunables.minimum_stop_distance_atr.toFixed(2)}–{config.tunables.maximum_stop_distance_atr.toFixed(2)} ATR · opposing room ≥ {config.tunables.minimum_room_to_opposing_sr_r.toFixed(2)}R</span>
            </div>
            <div className="flex items-center justify-between p-2 bg-slate-950/60 rounded border border-slate-800/60">
              <span className="text-slate-300">Session / trigger lifecycle</span>
              <span className="text-cyan-300 font-bold">{config.tunables.entry_session_start}–{config.tunables.entry_session_end} · {config.tunables.trigger_validity_bars} bars · force {config.tunables.forced_exit_time}</span>
            </div>
          </div>
          <p className="text-[10px] text-slate-500 mt-3">Directional pass/fail values are shown in the Live Trigger Radar below.</p>
        </div>

        {/* Strategy B Card */}
        <div className="bg-slate-900/90 border border-slate-800 rounded-xl p-5 shadow-lg">
          <div className="flex items-center justify-between pb-3 border-b border-slate-800 mb-4">
            <div>
              <div className="text-xs font-bold text-amber-400 uppercase tracking-wider">Strategy B (Expansion)</div>
              <h4 className="text-base font-bold text-slate-100">Volatility Compression Breakout</h4>
            </div>
            <div className="text-right">
              <span
                className={`px-2.5 py-1 rounded text-xs font-bold ${
                  strategies.volatility_breakout.state === "TRIGGERED"
                    ? "bg-emerald-500/20 text-emerald-300 border border-emerald-500/30 animate-pulse"
                    : "bg-slate-800 text-slate-300"
                }`}
              >
                {strategies.volatility_breakout.state}
              </span>
            </div>
          </div>

          <p className="text-xs text-slate-400 mb-4">
            Identifies tight volatility consolidation (contracted BBWidth, low ATR) and enters on an explosive volume-backed expansion breakout beyond the range boundary.
          </p>

          <div className="space-y-2 text-xs">
            <div className="flex items-center justify-between p-2 bg-slate-950/60 rounded border border-slate-800/60">
              <span className="text-slate-300">BBWidth &lt;= 25th Percentile (Compression)</span>
              <span className={features.bb_width_percentile <= 35 ? "text-emerald-400 font-bold" : "text-slate-500"}>
                {features.bb_width_percentile}% ({features.bb_width_percentile <= 35 ? "COMPRESSED" : "NORMAL"})
              </span>
            </div>

            <div className="flex items-center justify-between p-2 bg-slate-950/60 rounded border border-slate-800/60">
              <span className="text-slate-300">Consolidation Duration (4-12 Bars, Height &lt;= 1.75 ATR)</span>
              <span className="text-emerald-400 font-bold">MONITORED ON 5m</span>
            </div>

            <div className="flex items-center justify-between p-2 bg-slate-950/60 rounded border border-slate-800/60">
              <span className="text-slate-300">Breakout Close &gt; Range High + 0.10 ATR</span>
              <span className="text-slate-400">AWAITING EXPANSION</span>
            </div>

            <div className="flex items-center justify-between p-2 bg-slate-950/60 rounded border border-slate-800/60">
              <span className="text-slate-300">Futures RVOL &gt;= 1.30 on Breakout</span>
              <span className={features.rvol_5m >= 1.3 ? "text-emerald-400 font-bold" : "text-slate-400"}>
                RVOL: {features.rvol_5m}
              </span>
            </div>

            <div className="flex items-center justify-between p-2 bg-slate-950/60 rounded border border-slate-800/60">
              <span className="text-slate-300">Candle Body &gt;= 60% of Total Candle Range</span>
              <span className="text-emerald-400 font-bold">IMPULSE REQUIRED</span>
            </div>

            <div className="flex items-center justify-between p-2 bg-slate-950/60 rounded border border-slate-800/60">
              <span className="text-slate-300">Immediate False-Breakout Return Stop</span>
              <span className="text-emerald-400 font-bold">FAIL-FAST ACTIVE</span>
            </div>
          </div>
        </div>
      </div>

      {/* Global Override / Force Modal */}
      <OverrideModal
        isOpen={isOverrideModalOpen}
        onClose={() => setIsOverrideModalOpen(false)}
        onSuccess={() => {
          onRefresh();
          setIsOverrideModalOpen(false);
        }}
        defaultCap={config.option_selection.max_option_premium}
      />
    </div>
  );
};
