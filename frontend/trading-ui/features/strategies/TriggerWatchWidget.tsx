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
  HelpCircle,
  Layers,
  Play,
  RefreshCw,
  Shield,
  ShieldAlert,
  ShieldCheck,
  Sliders,
  TrendingDown,
  TrendingUp,
  XCircle,
  Zap,
} from "lucide-react";
import {
  StrategyTriggerDiagnosticsData,
  TriggerDiagnosticsResponseData,
  StrategyStatusData,
} from "../../lib/api";
import { OverrideModal } from "./OverrideModal";

interface TriggerWatchWidgetProps {
  diagnostics?: TriggerDiagnosticsResponseData | null;
  onRefresh: () => void;
  defaultCap?: number;
  marketData?: StrategyStatusData["market_data"];
}

export const TriggerWatchWidget: React.FC<TriggerWatchWidgetProps> = ({
  diagnostics,
  onRefresh,
  defaultCap = 70.0,
  marketData,
}) => {
  const [selectedStrategyIndex, setSelectedStrategyIndex] = useState<number>(0);
  const [isOverrideModalOpen, setIsOverrideModalOpen] = useState<boolean>(false);

  if (!diagnostics || !diagnostics.strategies || diagnostics.strategies.length === 0) {
    return (
      <div className="bg-slate-900/90 border border-slate-800 rounded-xl p-6 text-center shadow-lg">
        <div className="flex items-center justify-center gap-2 text-slate-400 text-sm">
          <RefreshCw className="w-4 h-4 animate-spin text-cyan-400" />
          <span>Computing real-time strategy trigger diagnostics...</span>
        </div>
      </div>
    );
  }

  const { gates, strategies, active_overrides } = diagnostics;
  const currentStrategy: StrategyTriggerDiagnosticsData =
    strategies[selectedStrategyIndex] || strategies[0];

  const isBypassed = active_overrides?.bypass_entry_window;
  const activeCap = active_overrides?.max_option_premium_cap || defaultCap;

  return (
    <div className="bg-slate-900/95 border border-slate-800 rounded-xl overflow-hidden shadow-xl">
      {/* 1. Header with Override Action */}
      <div className="bg-slate-950 px-5 py-4 border-b border-slate-800 flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2.5">
          <div className="w-8 h-8 rounded-lg bg-cyan-500/10 text-cyan-400 border border-cyan-500/20 flex items-center justify-center">
            <Activity className="w-4 h-4" />
          </div>
          <div>
            <h3 className="text-sm font-bold text-slate-100 tracking-wide uppercase flex items-center gap-2">
              What Is The Trade Waiting For?
              <span className="text-[10px] px-2 py-0.5 rounded-full bg-cyan-500/20 text-cyan-300 font-semibold border border-cyan-500/30">
                LIVE TRIGGER RADAR
              </span>
            </h3>
            <p className="text-xs text-slate-400">
              Condition-by-condition diagnostic breakdown of upcoming trade triggers and session gates.
            </p>
          </div>
        </div>

        <div className="flex items-center gap-2">
          <button
            onClick={() => setIsOverrideModalOpen(true)}
            className="px-3 py-1.5 rounded-lg text-xs font-bold text-amber-300 bg-amber-500/10 hover:bg-amber-500/20 border border-amber-500/30 transition flex items-center gap-1.5 shadow-sm"
          >
            <Sliders className="w-3.5 h-3.5" />
            <span>⚡ Override / Force Trigger</span>
          </button>
          <button
            onClick={onRefresh}
            title="Refresh trigger diagnostics"
            className="p-1.5 rounded-lg text-slate-400 hover:text-slate-200 hover:bg-slate-800 transition border border-slate-800"
          >
            <RefreshCw className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>

      {/* 2. Primary Blocker Banner */}
      <div className="px-5 pt-4 pb-2">
        <div
          className={`p-3 rounded-lg border flex items-start gap-2.5 text-xs ${
            gates.primary_blocker?.includes("clear") || currentStrategy.overall_status === "READY_TO_TRIGGER"
              ? "bg-emerald-500/10 border-emerald-500/30 text-emerald-300"
              : "bg-amber-500/10 border-amber-500/30 text-amber-300"
          }`}
        >
          {gates.primary_blocker?.includes("clear") ? (
            <CheckCircle2 className="w-4 h-4 text-emerald-400 flex-shrink-0 mt-0.5" />
          ) : (
            <AlertCircle className="w-4 h-4 text-amber-400 flex-shrink-0 mt-0.5" />
          )}
          <div className="flex-1">
            <span className="font-bold uppercase tracking-wider block text-[10px] text-slate-400">
              Session Gate & Technical Radar Status
            </span>
            <span className="text-xs text-slate-200 font-medium">
              {gates.primary_blocker || currentStrategy.key_blocker}
            </span>
          </div>
          {isBypassed && (
            <span className="px-2 py-0.5 rounded text-[10px] font-bold bg-amber-500/20 text-amber-300 border border-amber-500/40">
              WINDOW BYPASSED
            </span>
          )}
        </div>
      </div>

      {currentStrategy.strategy === "TREND_PULLBACK" &&
        (currentStrategy.key_blocker.includes("FUTURES") || marketData?.last_error) && (
        <div className="px-5 pb-2">
          <div className="rounded-lg border border-slate-700/80 bg-slate-950/70 p-3 text-[11px]">
            <div className="font-bold uppercase tracking-wider text-cyan-300 mb-2">
              Strategy A Futures Data Path
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-2 text-slate-400">
              <div>Provider: <span className="text-slate-200 font-mono">{(marketData?.provider || "unknown").toUpperCase()}</span></div>
              <div>Session: <span className={marketData?.provider_active ? "text-emerald-400" : "text-rose-400"}>{marketData?.provider_active ? "ACTIVE" : "INACTIVE"}</span></div>
              <div>Contract: <span className="text-slate-200 font-mono">{marketData?.futures_instrument || "UNRESOLVED"}</span></div>
              <div>15m bars: <span className="text-slate-200 font-mono">{marketData?.futures_candle_count ?? 0}</span></div>
            </div>
            <div className="mt-2 text-slate-400">
              Latest completed bar: <span className="text-slate-200 font-mono">{marketData?.latest_futures_candle || "NONE"}</span>
            </div>
            {marketData?.last_error && (
              <div className="mt-1 text-amber-300">
                Data-path status: <span className="font-mono font-bold">{marketData.last_error}</span>
              </div>
            )}
          </div>
        </div>
      )}

      {/* 3. System Gatekeeper Micro-Bar */}
      <div className="px-5 py-3">
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-2">
          {/* Gate 1: Session Window */}
          <div className="bg-slate-950/70 p-2 rounded-lg border border-slate-800/80">
            <div className="text-[10px] text-slate-400 uppercase tracking-wider mb-1 flex items-center gap-1">
              <Clock className="w-3 h-3 text-blue-400" />
              Window
            </div>
            <div className="text-xs font-semibold">
              {gates.within_trading_window ? (
                <span className="text-emerald-400">Active (09:30-14:45)</span>
              ) : (
                <span className="text-rose-400 font-medium">Closed</span>
              )}
            </div>
          </div>

          {/* Gate 2: Auto-Trading */}
          <div className="bg-slate-950/70 p-2 rounded-lg border border-slate-800/80">
            <div className="text-[10px] text-slate-400 uppercase tracking-wider mb-1 flex items-center gap-1">
              <Zap className="w-3 h-3 text-amber-400" />
              Auto Exec
            </div>
            <div className="text-xs font-semibold">
              {gates.auto_trade_enabled ? (
                <span className="text-emerald-400">Enabled</span>
              ) : (
                <span className="text-slate-500">Disabled</span>
              )}
            </div>
          </div>

          {/* Gate 3: Positions */}
          <div className="bg-slate-950/70 p-2 rounded-lg border border-slate-800/80">
            <div className="text-[10px] text-slate-400 uppercase tracking-wider mb-1 flex items-center gap-1">
              <Shield className="w-3 h-3 text-indigo-400" />
              Positions
            </div>
            <div className="text-xs font-semibold">
              {gates.max_positions_reached ? (
                <span className="text-amber-400">Cap Reached</span>
              ) : (
                <span className="text-emerald-400">Available</span>
              )}
            </div>
          </div>

          {/* Gate 4: Cooldown */}
          <div className="bg-slate-950/70 p-2 rounded-lg border border-slate-800/80">
            <div className="text-[10px] text-slate-400 uppercase tracking-wider mb-1 flex items-center gap-1">
              <Activity className="w-3 h-3 text-emerald-400" />
              Loss Cooldown
            </div>
            <div className="text-xs font-semibold">
              {gates.in_cooldown ? (
                <span className="text-amber-400">Cooling down</span>
              ) : (
                <span className="text-emerald-400">Clear (Ready)</span>
              )}
            </div>
          </div>

          {/* Gate 5: Daily Limit */}
          <div className="bg-slate-950/70 p-2 rounded-lg border border-slate-800/80">
            <div className="text-[10px] text-slate-400 uppercase tracking-wider mb-1 flex items-center gap-1">
              <Layers className="w-3 h-3 text-purple-400" />
              Daily Trades
            </div>
            <div className="text-xs font-semibold text-slate-200">
              {gates.daily_trades_count} / {gates.daily_trades_max} Taken
            </div>
          </div>

          {/* Gate 6: Premium Cap */}
          <div className="bg-slate-950/70 p-2 rounded-lg border border-slate-800/80">
            <div className="text-[10px] text-slate-400 uppercase tracking-wider mb-1 flex items-center gap-1">
              <Flame className="w-3 h-3 text-cyan-400" />
              Option Cap
            </div>
            <div className="text-xs font-bold text-cyan-300">
              ₹{activeCap.toFixed(0)}
              {active_overrides?.max_option_premium_cap && (
                <span className="text-[9px] ml-1 text-amber-400 font-normal">(override)</span>
              )}
            </div>
          </div>
        </div>
      </div>

      {/* 4. Strategy Selector Tabs */}
      <div className="px-5 border-t border-slate-800/60 pt-3">
        <div className="flex flex-wrap gap-2 mb-3">
          {strategies.map((st, idx) => {
            const isSelected = idx === selectedStrategyIndex;
            const isBull = st.direction === "BULLISH";
            return (
              <button
                key={`${st.strategy}-${st.direction}`}
                onClick={() => setSelectedStrategyIndex(idx)}
                className={`px-3 py-1.5 rounded-lg text-xs font-bold transition flex items-center gap-2 border ${
                  isSelected
                    ? isBull
                      ? "bg-emerald-500/20 border-emerald-500/50 text-emerald-300 shadow-sm"
                      : "bg-rose-500/20 border-rose-500/50 text-rose-300 shadow-sm"
                    : "bg-slate-950/60 border-slate-800 text-slate-400 hover:border-slate-700 hover:text-slate-300"
                }`}
              >
                {isBull ? (
                  <TrendingUp className="w-3.5 h-3.5 text-emerald-400" />
                ) : (
                  <TrendingDown className="w-3.5 h-3.5 text-rose-400" />
                )}
                <span>{st.strategy_label}</span>
                <span
                  className={`px-1.5 py-0.2 rounded text-[10px] font-mono ${
                    st.overall_status === "READY_TO_TRIGGER"
                      ? "bg-emerald-500 text-black font-bold"
                      : "bg-slate-800 text-slate-300"
                  }`}
                >
                  {st.passed_count}/{st.total_count}
                </span>
              </button>
            );
          })}
        </div>
      </div>

      {/* 5. Selected Strategy Summary Card & Progress */}
      <div className="px-5 py-3 bg-slate-950/40 border-y border-slate-800/60">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 mb-2">
          <div>
            <div className="text-xs font-bold text-slate-200 flex items-center gap-2">
              <span>{currentStrategy.strategy_label}</span>
              <span
                className={`px-2 py-0.5 rounded text-[10px] font-bold ${
                  currentStrategy.overall_status === "READY_TO_TRIGGER"
                    ? "bg-emerald-500/20 text-emerald-300 border border-emerald-500/30 animate-pulse"
                    : "bg-amber-500/10 text-amber-300 border border-amber-500/30"
                }`}
              >
                {currentStrategy.overall_status === "READY_TO_TRIGGER"
                  ? "READY TO TRIGGER"
                  : "AWAITING MARKET CONDITIONS"}
              </span>
            </div>
            <div className="text-[11px] text-slate-400 mt-0.5">
              Key Blocker: <span className="text-slate-200 font-medium">{currentStrategy.key_blocker}</span>
            </div>
          </div>

          <div className="text-right sm:text-right">
            <div className="text-[11px] text-slate-400">
              {currentStrategy.strategy === "TREND_PULLBACK" ? "Current Futures" : "Current Spot"}: <span className="font-mono text-slate-200 font-bold">₹{currentStrategy.current_spot.toFixed(2)}</span>
              {currentStrategy.target_entry_level && (
                <span className="ml-2">
                  Target: <span className="font-mono text-cyan-300 font-bold">₹{currentStrategy.target_entry_level.toFixed(2)}</span>
                  <span className="ml-1 text-[10px] text-amber-400">({currentStrategy.distance_pts} pts gap)</span>
                </span>
              )}
            </div>
          </div>
        </div>

        {/* Condition Progress Bar */}
        <div className="space-y-1">
          <div className="flex justify-between text-[11px] text-slate-400">
            <span>
              Condition Checklist Progress: <span className="text-slate-200 font-bold">{currentStrategy.passed_count} of {currentStrategy.total_count} satisfied</span>
            </span>
            <span className="font-bold text-cyan-400">{currentStrategy.ready_pct}% Complete</span>
          </div>
          <div className="w-full h-2 bg-slate-800 rounded-full overflow-hidden">
            <div
              className={`h-full transition-all duration-300 ${
                currentStrategy.ready_pct === 100
                  ? "bg-emerald-500"
                  : currentStrategy.ready_pct >= 70
                  ? "bg-cyan-500"
                  : "bg-amber-500"
              }`}
              style={{ width: `${currentStrategy.ready_pct}%` }}
            />
          </div>
        </div>
      </div>

      {/* 5.5. Strategy Phase State Machine Pipeline (Section 21) */}
      {currentStrategy.phase_summary && (
        <div className="px-5 py-3 border-b border-slate-800/60 bg-slate-950/20">
          <div className="text-[10px] text-slate-400 font-bold uppercase tracking-wider mb-2 flex items-center gap-1.5">
            <Layers className="w-3 h-3 text-cyan-400" />
            <span>Strategy State Machine Lifecycle</span>
            {currentStrategy.phase_state && (
              <span className="ml-auto px-2 py-0.5 rounded text-[9px] font-mono font-bold bg-cyan-500/10 text-cyan-300 border border-cyan-500/30">
                ACTIVE PHASE: {currentStrategy.phase_state}
              </span>
            )}
          </div>
          {currentStrategy.phase_summary.compression ? (
            /* Strategy B Pipeline: Compression -> Box -> Trigger -> Anti-Chase -> Confirmation -> Risk */
            <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-2">
              {/* Phase 1: Compression */}
              <div className={`p-2.5 rounded-lg border text-xs ${
                currentStrategy.phase_summary.compression?.is_compressed
                  ? "bg-emerald-500/10 border-emerald-500/30 text-emerald-300"
                  : "bg-slate-950/80 border-slate-800 text-slate-300"
              }`}>
                <div className="text-[10px] uppercase font-bold text-slate-400 mb-0.5">1. Compression</div>
                <div className="font-bold">{currentStrategy.phase_summary.compression?.is_compressed ? "COMPRESSED" : "NORMAL"}</div>
                <div className="text-[10px] text-slate-400 mt-1">
                  BB: {currentStrategy.phase_summary.compression?.bb_percentile}th %ile
                </div>
              </div>

              {/* Phase 2: Box */}
              <div className={`p-2.5 rounded-lg border text-xs ${
                currentStrategy.phase_summary.box?.status === "LOCKED"
                  ? "bg-emerald-500/10 border-emerald-500/30 text-emerald-300"
                  : "bg-slate-950/80 border-slate-800 text-slate-300"
              }`}>
                <div className="text-[10px] uppercase font-bold text-slate-400 mb-0.5">2. Locked Box</div>
                <div className="font-bold">{currentStrategy.phase_summary.box?.status || "SEARCHING"}</div>
                <div className="text-[10px] text-slate-400 mt-1 truncate">
                  {currentStrategy.phase_summary.box?.height_pts ? `${currentStrategy.phase_summary.box.height_pts} pts (${currentStrategy.phase_summary.box.bars_active}/8b)` : "Scanning"}
                </div>
              </div>

              {/* Phase 3: Trigger */}
              <div className={`p-2.5 rounded-lg border text-xs ${
                currentStrategy.phase_summary.trigger?.gap_pts === 0
                  ? "bg-emerald-500/10 border-emerald-500/30 text-emerald-300"
                  : "bg-amber-500/10 border-amber-500/30 text-amber-300"
              }`}>
                <div className="text-[10px] uppercase font-bold text-slate-400 mb-0.5">3. Trigger</div>
                <div className="font-bold truncate">{currentStrategy.phase_summary.trigger?.waiting_for || "Awaiting"}</div>
                <div className="text-[10px] text-slate-400 mt-1">
                  {currentStrategy.phase_summary.trigger?.gap_pts ? `${currentStrategy.phase_summary.trigger.gap_pts} pts gap` : "Triggered"}
                </div>
              </div>

              {/* Phase 4: Anti-Chase Extension */}
              <div className={`p-2.5 rounded-lg border text-xs ${
                currentStrategy.phase_summary.extension?.status === "VALID"
                  ? "bg-emerald-500/10 border-emerald-500/30 text-emerald-300"
                  : "bg-rose-500/10 border-rose-500/30 text-rose-300"
              }`}>
                <div className="text-[10px] uppercase font-bold text-slate-400 mb-0.5">4. Extension</div>
                <div className="font-bold">{currentStrategy.phase_summary.extension?.status || "VALID"}</div>
                <div className="text-[10px] text-slate-400 mt-1">
                  {currentStrategy.phase_summary.extension?.extension_atr ?? 0} ATR (≤ 0.75)
                </div>
              </div>

              {/* Phase 5: Confirmation */}
              <div className={`p-2.5 rounded-lg border text-xs ${
                (currentStrategy.phase_summary.confirmation?.score ?? 0) >= (currentStrategy.phase_summary.confirmation?.required ?? 3)
                  ? "bg-emerald-500/10 border-emerald-500/30 text-emerald-300"
                  : "bg-slate-950/80 border-slate-800 text-slate-300"
              }`}>
                <div className="text-[10px] uppercase font-bold text-slate-400 mb-0.5">5. Confirmation</div>
                <div className="font-bold">
                  {currentStrategy.phase_summary.confirmation?.score ?? 0} / {currentStrategy.phase_summary.confirmation?.required ?? 3} pts
                </div>
                <div className="text-[10px] text-slate-400 mt-1">
                  {(currentStrategy.phase_summary.confirmation?.score ?? 0) >= (currentStrategy.phase_summary.confirmation?.required ?? 3) ? "Sufficient" : "Pending factors"}
                </div>
              </div>

              {/* Phase 6: Structural Risk */}
              <div className="p-2.5 rounded-lg border border-slate-800 bg-slate-950/80 text-xs text-slate-300">
                <div className="text-[10px] uppercase font-bold text-slate-400 mb-0.5">6. Structural Risk</div>
                <div className="font-bold text-cyan-300">
                  {currentStrategy.phase_summary.risk?.initial_r_atr ? `${currentStrategy.phase_summary.risk.initial_r_atr} ATR` : "Band OK"}
                </div>
                <div className="text-[10px] text-slate-400 mt-1 truncate">
                  SL: ₹{currentStrategy.phase_summary.risk?.stop ?? "-"}
                </div>
              </div>
            </div>
          ) : (
            /* Strategy A Pipeline: Regime -> Impulse -> Pullback -> Trigger -> Confirmation -> Risk */
            <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-2">
              {/* Phase 1: Macro Regime */}
              <div className={`p-2.5 rounded-lg border text-xs ${
                currentStrategy.phase_summary.regime?.status === "QUALIFIED"
                  ? "bg-emerald-500/10 border-emerald-500/30 text-emerald-300"
                  : "bg-slate-950/80 border-slate-800 text-slate-300"
              }`}>
                <div className="text-[10px] uppercase font-bold text-slate-400 mb-0.5">1. Regime</div>
                <div className="font-bold">{currentStrategy.phase_summary.regime?.status || "WAITING"}</div>
                <div className="text-[10px] text-slate-400 mt-1">
                  {currentStrategy.phase_summary.regime?.qualified ? "Qualified" : "Not qualified"} | {currentStrategy.phase_summary.regime?.direction_score} | ADX {currentStrategy.phase_summary.regime?.adx}
                </div>
                <div className="text-[10px] text-slate-500 mt-1 truncate" title={currentStrategy.phase_summary.regime?.qualified_since || ""}>
                  {currentStrategy.phase_summary.setup_cutoff_event || "NONE"} cutoff · {currentStrategy.phase_summary.regime_first_qualified_at || "not established"}
                </div>
              </div>

              {/* Phase 2: Impulse */}
              <div className={`p-2.5 rounded-lg border text-xs ${
                currentStrategy.phase_summary.impulse?.found
                  ? "bg-emerald-500/10 border-emerald-500/30 text-emerald-300"
                  : "bg-slate-950/80 border-slate-800 text-slate-300"
              }`}>
                <div className="text-[10px] uppercase font-bold text-slate-400 mb-0.5">2. Impulse</div>
                <div className="font-bold">{currentStrategy.phase_summary.impulse?.found ? "FOUND (YES)" : "PENDING"}</div>
                <div className="text-[10px] text-slate-400 mt-1">
                  {currentStrategy.phase_summary.impulse?.height_atr ? `${currentStrategy.phase_summary.impulse.height_atr} ATR` : "Waiting for swing"}
                </div>
                <div className="text-[10px] text-slate-500 mt-1 space-y-0.5">
                  <div>
                    Search: {currentStrategy.phase_summary.impulse_search?.pre_cutoff_candles_included ?? 0} pre-cutoff / {currentStrategy.phase_summary.impulse_search?.post_cutoff_candles_searched ?? 0} post-cutoff candles
                  </div>
                  <div className="truncate">
                    Cutoff: {currentStrategy.phase_summary.impulse_search?.setup_cutoff_timestamp || "none"} · Start: {currentStrategy.phase_summary.impulse_search?.search_start_timestamp || "none"}
                  </div>
                  <div>
                    Age: {currentStrategy.phase_summary.setup_age_seconds != null ? `${Math.round(currentStrategy.phase_summary.setup_age_seconds / 60)}m` : "-"} · {currentStrategy.phase_summary.completed_5m_candles_since_cutoff ?? 0} completed 5m candles since cutoff
                  </div>
                  {currentStrategy.phase_summary.impulse?.impulse_start && (
                    <div className="truncate">
                      {currentStrategy.phase_summary.impulse.impulse_direction} {currentStrategy.phase_summary.impulse.impulse_points?.toFixed(1)} pts · {currentStrategy.phase_summary.impulse.impulse_start} → {currentStrategy.phase_summary.impulse.impulse_end} · {currentStrategy.phase_summary.impulse.impulse_crossed_setup_cutoff ? "crossed cutoff" : "after cutoff"}
                    </div>
                  )}
                  {!currentStrategy.phase_summary.impulse?.found && currentStrategy.phase_summary.impulse_rejection_reason && (
                    <div className="text-amber-400 truncate" title={currentStrategy.phase_summary.impulse_rejection_reason}>
                      {currentStrategy.phase_summary.impulse_rejection_reason}
                    </div>
                  )}
                </div>
              </div>

              {/* Phase 3: Pullback */}
              <div className={`p-2.5 rounded-lg border text-xs ${
                currentStrategy.phase_summary.pullback?.state === "QUALIFIED"
                  ? "bg-emerald-500/10 border-emerald-500/30 text-emerald-300"
                  : "bg-slate-950/80 border-slate-800 text-slate-300"
              }`}>
                <div className="text-[10px] uppercase font-bold text-slate-400 mb-0.5">3. Pullback</div>
                <div className="font-bold">{currentStrategy.phase_summary.pullback?.state || "WAITING"}</div>
                <div className="text-[10px] text-slate-400 mt-1">
                  {currentStrategy.phase_summary.pullback?.bars ?? 0} bars | {currentStrategy.phase_summary.pullback?.depth_pct ?? 0}%
                </div>
                <div className="text-[10px] text-cyan-300 mt-1">
                  Required pullback depth: {currentStrategy.phase_summary.pullback?.depth_range || "configured range"}
                </div>
              </div>

              {/* Phase 4: Trigger */}
              <div className={`p-2.5 rounded-lg border text-xs ${
                currentStrategy.phase_summary.trigger?.gap_pts === 0
                  ? "bg-emerald-500/10 border-emerald-500/30 text-emerald-300"
                  : "bg-amber-500/10 border-amber-500/30 text-amber-300"
              }`}>
                <div className="text-[10px] uppercase font-bold text-slate-400 mb-0.5">4. Trigger</div>
                <div className="font-bold truncate">{currentStrategy.phase_summary.trigger?.waiting_for || "Awaiting"}</div>
                <div className="text-[10px] text-slate-400 mt-1">
                  {currentStrategy.phase_summary.trigger?.gap_pts ? `${currentStrategy.phase_summary.trigger.gap_pts} pts gap` : "Triggered"}
                </div>
              </div>

              {/* Phase 5: Confirmation */}
              <div className={`p-2.5 rounded-lg border text-xs ${
                (currentStrategy.phase_summary.confirmation?.score ?? 0) >= (currentStrategy.phase_summary.confirmation?.required ?? 2)
                  ? "bg-emerald-500/10 border-emerald-500/30 text-emerald-300"
                  : "bg-slate-950/80 border-slate-800 text-slate-300"
              }`}>
                <div className="text-[10px] uppercase font-bold text-slate-400 mb-0.5">5. Confirmation</div>
                <div className="font-bold">
                  {currentStrategy.phase_summary.confirmation?.score ?? 0} / {currentStrategy.phase_summary.confirmation?.required ?? 2} pts
                </div>
                <div className="text-[10px] text-slate-400 mt-1">
                  {(currentStrategy.phase_summary.confirmation?.score ?? 0) >= (currentStrategy.phase_summary.confirmation?.required ?? 2) ? "Sufficient" : "Pending factors"}
                </div>
              </div>

              {/* Phase 6: Structural Risk */}
              <div className="p-2.5 rounded-lg border border-slate-800 bg-slate-950/80 text-xs text-slate-300">
                <div className="text-[10px] uppercase font-bold text-slate-400 mb-0.5">6. Structural Risk</div>
                <div className="font-bold text-cyan-300">
                  {currentStrategy.phase_summary.risk?.initial_r_atr ? `${currentStrategy.phase_summary.risk.initial_r_atr} ATR` : "Band OK"}
                </div>
                <div className="text-[10px] text-slate-400 mt-1 truncate">
                  SL: ₹{currentStrategy.phase_summary.risk?.stop ?? "-"}
                </div>
              </div>
            </div>
          )}
        </div>
      )}

      {/* 6. Granular Condition Checklist Table */}
      <div className="px-5 py-4 overflow-x-auto">
        <table className="w-full text-left text-xs">
          <thead>
            <tr className="text-[10px] text-slate-400 uppercase tracking-wider border-b border-slate-800">
              <th className="pb-2 font-medium">Condition / Rule</th>
              <th className="pb-2 font-medium">Current Live Value</th>
              <th className="pb-2 font-medium">Required Target</th>
              <th className="pb-2 font-medium text-center">Status</th>
              <th className="pb-2 font-medium">Distance / Gap Detail</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-800/40">
            {currentStrategy.conditions.map((cond) => {
              const isPassed = cond.status === "PASSED";
              const isNotApplicable = cond.status === "N/A";
              return (
                <tr key={cond.id} className="hover:bg-slate-800/30 transition">
                  <td className="py-2.5 pr-2 font-medium text-slate-200">
                    {cond.name}
                  </td>
                  <td className="py-2.5 pr-2 font-mono text-slate-300">
                    {cond.current_value}
                  </td>
                  <td className="py-2.5 pr-2 font-mono text-slate-400">
                    {cond.target_threshold}
                  </td>
                  <td className="py-2.5 pr-2 text-center">
                    {isPassed ? (
                      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-bold bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
                        <CheckCircle2 className="w-3 h-3" />
                        PASSED
                      </span>
                    ) : isNotApplicable ? (
                      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-bold bg-slate-500/10 text-slate-400 border border-slate-500/20">
                        N/A
                      </span>
                    ) : (
                      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-bold bg-amber-500/10 text-amber-400 border border-amber-500/20">
                        <Clock className="w-3 h-3" />
                        PENDING
                      </span>
                    )}
                  </td>
                  <td className="py-2.5 pl-2 text-slate-400">
                    <span className={isPassed ? "text-emerald-400/80" : isNotApplicable ? "text-slate-500" : "text-amber-300/90 font-medium"}>
                      {cond.gap_description}
                    </span>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {/* Modal Dialog */}
      <OverrideModal
        isOpen={isOverrideModalOpen}
        onClose={() => setIsOverrideModalOpen(false)}
        onSuccess={() => {
          onRefresh();
          setIsOverrideModalOpen(false);
        }}
        defaultCap={defaultCap}
      />
    </div>
  );
};
