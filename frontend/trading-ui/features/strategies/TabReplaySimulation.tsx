"use client";

import React, { useState, useEffect } from "react";
import {
  Activity,
  AlertCircle,
  AlertTriangle,
  ArrowDownRight,
  ArrowRight,
  ArrowUpRight,
  Calendar,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  Clock,
  ExternalLink,
  Flame,
  Layers,
  Play,
  RefreshCw,
  Sliders,
  TrendingDown,
  TrendingUp,
  XCircle,
  Zap,
} from "lucide-react";
import {
  SimulatedTradeRecordData,
  SimulationBarSnapshotData,
  SimulationResultData,
  fetchSimulationAvailableDates,
  runStrategySimulation,
} from "../../lib/api";

const formatPnl = (value: number | null | undefined): string =>
  value == null ? "N/A" : `${value >= 0 ? "+" : ""}\u20b9${value.toLocaleString()}`;

const formatAmount = (value: number | null | undefined): string =>
  value == null ? "N/A" : `\u20b9${value.toLocaleString()}`;

interface TabReplaySimulationProps {
  historicalSource?: "BREEZE" | "KITE";
}

export const TabReplaySimulation: React.FC<TabReplaySimulationProps> = ({
  historicalSource = "BREEZE",
}) => {
  const [availableDates, setAvailableDates] = useState<string[]>([]);
  const [selectedDate, setSelectedDate] = useState<string>("");
  const [isRunning, setIsRunning] = useState<boolean>(false);
  const [result, setResult] = useState<SimulationResultData | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  // Timeline scrubber state
  const [selectedBarIndex, setSelectedBarIndex] = useState<number>(0);

  // Overrides panel state
  const [showOverrides, setShowOverrides] = useState<boolean>(false);
  const [rvolThreshold, setRvolThreshold] = useState<number>(1.20);
  const [stratBMinConf, setStratBMinConf] = useState<number>(3);
  const [boxMaxHeightAtr, setBoxMaxHeightAtr] = useState<number>(1.30);
  const [bypassWindow, setBypassWindow] = useState<boolean>(false);

  useEffect(() => {
    setResult(null);
    loadDates();
  }, [historicalSource]);

  const loadDates = async () => {
    try {
      const dates = await fetchSimulationAvailableDates(historicalSource);
      if (dates && dates.length > 0) {
        setAvailableDates(dates);
        setSelectedDate(dates[0]);
      }
    } catch (err: any) {
      console.error("Failed to load available dates", err);
      setErrorMsg(err instanceof Error ? err.message : "Failed to load available simulation dates.");
    }
  };

  const handleRunSimulation = async (dateOverride?: string) => {
    const targetDate = dateOverride || selectedDate;
    if (!targetDate) return;
    setIsRunning(true);
    setErrorMsg(null);
    try {
      const res = await runStrategySimulation({
        date: targetDate,
        overrides: {
          rvol_threshold: Number(rvolThreshold),
          strat_b_min_confirmation: Number(stratBMinConf),
          box_max_height_atr: Number(boxMaxHeightAtr),
        },
        bypass_window: bypassWindow,
        historical_source: historicalSource,
      });
      setResult(res);
      setSelectedBarIndex(0);
    } catch (err: any) {
      setErrorMsg(err.message || "Simulation failed to run.");
    } finally {
      setIsRunning(false);
    }
  };

  const currentBar: SimulationBarSnapshotData | null =
    result?.timeline && result.timeline.length > 0
      ? result.timeline[Math.min(selectedBarIndex, result.timeline.length - 1)]
      : null;
  const trades = result?.trades ?? [];
  const netPnl = result?.net_pnl;
  const grossPnl = result?.total_pnl;
  const hasNetPnl = netPnl !== null && netPnl !== undefined;
  const replayDiagnostics = result?.replay_metadata?.strategy_a_replay_diagnostics as
    | {
        directional_evaluations?: number;
        completed_bar_checks?: number;
        ready_direction_checks?: number;
        blocker_counts?: Record<string, number>;
        data_quality_counts?: Record<string, number>;
        event_counts?: Record<string, number>;
        setup_count?: number;
        signal_count?: number;
        resolved_trade_count?: number;
        unresolved_trade_count?: number;
        ambiguous_trade_count?: number;
        futures_entry_window_coverage?: {
          expected_15m_bars?: number;
          available_15m_bars?: number;
          coverage_pct?: number;
          missing_15m_bar_ends_ist?: string[];
        };
        gate_funnel?: Record<string, { evaluated?: number; passed?: number; pass_pct?: number }>;
        component_funnel?: Record<string, { evaluated?: number; passed?: number; pass_pct?: number }>;
      }
    | undefined;
  const blockerEntries = Object.entries(replayDiagnostics?.blocker_counts || {}).slice(0, 5);
  const dataQualityEntries = Object.entries(replayDiagnostics?.data_quality_counts || {});
  const gateFunnelEntries = Object.entries(replayDiagnostics?.gate_funnel || {});
  const trendComponentEntries = Object.entries(replayDiagnostics?.component_funnel || {})
    .filter(([name]) => name.startsWith("trend."));
  const replayData = result?.replay_metadata?.data_fingerprint as
    | { missing_data?: string[]; source_diagnostics?: Record<string, any> }
    | undefined;
  const controlApplication = result?.replay_metadata?.control_application as
    | {
        applied_overrides?: Record<string, unknown>;
        not_applied_overrides?: Record<string, { value?: unknown; reason?: string }>;
        not_applied_request_controls?: Record<string, { value?: unknown; reason?: string }>;
      }
    | undefined;
  const appliedReplayControls = Object.entries(controlApplication?.applied_overrides || {});
  const ignoredReplayOverrides = Object.entries(controlApplication?.not_applied_overrides || {});
  const unsupportedRequestControls = Object.entries(controlApplication?.not_applied_request_controls || {});

  const jumpToNextEvent = () => {
    if (!result?.timeline) return;
    for (let i = selectedBarIndex + 1; i < result.timeline.length; i++) {
      if (result.timeline[i].event) {
        setSelectedBarIndex(i);
        return;
      }
    }
    // Loop back
    for (let i = 0; i <= selectedBarIndex; i++) {
      if (result.timeline[i].event) {
        setSelectedBarIndex(i);
        return;
      }
    }
  };

  return (
    <div className="space-y-6">
      <p className="text-sm text-amber-300">
        {result?.limitation || `Replay uses real completed ${historicalSource} spot/futures candles.`}
        {((replayData?.missing_data?.length ?? 0) > 0 || dataQualityEntries.length > 0)
          ? " Missing market history can suppress signals."
          : ""}
      </p>
      <div className="text-[11px] text-slate-400">
        Historical source: <span className="font-mono font-bold text-cyan-300">{historicalSource}</span>
      </div>
      {/* 1. Simulation Control & Session Selector */}
      <div className="bg-slate-900/95 border border-slate-800 rounded-xl p-5 shadow-xl">
        <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
          <div>
            <div className="flex items-center gap-2">
              <div className="w-8 h-8 rounded-lg bg-indigo-500/10 text-indigo-400 border border-indigo-500/20 flex items-center justify-center">
                <Play className="w-4 h-4 fill-indigo-400" />
              </div>
              <div>
                <h2 className="text-base font-bold text-slate-100 flex items-center gap-2">
                  Intraday Replay & Historical Simulation
                  <span className="text-[10px] px-2 py-0.5 rounded-full bg-indigo-500/20 text-indigo-300 font-semibold border border-indigo-500/30">
                    WHAT-IF ENGINE
                  </span>
                </h2>
                <p className="text-xs text-slate-400">
                  Walk forward bar-by-bar through any historical trading session to test if your strategy updates would have triggered trades.
                </p>
              </div>
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <button
              onClick={() => setShowOverrides(!showOverrides)}
              className={`px-3 py-2 rounded-lg text-xs font-semibold border transition flex items-center gap-1.5 ${
                showOverrides
                  ? "bg-cyan-500/20 text-cyan-300 border-cyan-500/40"
                  : "bg-slate-800 text-slate-300 border-slate-700 hover:bg-slate-750"
              }`}
            >
              <Sliders className="w-3.5 h-3.5" />
              <span>{showOverrides ? "Hide What-If Overrides" : "Tune What-If Overrides"}</span>
            </button>

            <button
              onClick={() => handleRunSimulation()}
              disabled={isRunning || !selectedDate}
              className="px-5 py-2 rounded-lg text-xs font-bold text-white bg-indigo-600 hover:bg-indigo-500 active:bg-indigo-700 disabled:opacity-50 transition flex items-center gap-2 shadow-lg shadow-indigo-600/20"
            >
              {isRunning ? (
                <>
                  <RefreshCw className="w-3.5 h-3.5 animate-spin" />
                  <span>Replaying Session...</span>
                </>
              ) : (
                <>
                  <Play className="w-3.5 h-3.5 fill-white" />
                  <span>Run Full-Day Simulation</span>
                </>
              )}
            </button>
          </div>
        </div>

        {/* Date Selection Chips */}
        <div className="mt-4 pt-3 border-t border-slate-800/80 flex flex-wrap items-center gap-2">
          <span className="text-xs text-slate-400 flex items-center gap-1 mr-1">
            <Calendar className="w-3.5 h-3.5 text-slate-500" />
            Session Date:
          </span>
          {availableDates.map((d) => (
            <button
              key={d}
              onClick={() => {
                setSelectedDate(d);
                handleRunSimulation(d);
              }}
              className={`px-2.5 py-1 rounded-md text-xs font-mono font-medium transition border ${
                selectedDate === d
                  ? "bg-indigo-500/20 border-indigo-500/50 text-indigo-300 font-bold"
                  : "bg-slate-950/60 border-slate-800 text-slate-400 hover:text-slate-200 hover:border-slate-700"
              }`}
            >
              {d}
            </button>
          ))}
          {/* Custom Date Input */}
          <input
            type="date"
            value={selectedDate}
            onChange={(e) => setSelectedDate(e.target.value)}
            className="bg-slate-950 border border-slate-800 text-slate-200 text-xs rounded-md px-2 py-1 font-mono focus:outline-none focus:border-indigo-500"
          />
        </div>

        {/* Expandable What-If Overrides Panel */}
        {showOverrides && (
          <div className="mt-4 pt-4 border-t border-slate-800 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3 bg-slate-950/60 p-4 rounded-lg border border-slate-800/80">
            <div className="sm:col-span-2 lg:col-span-3 rounded-lg border border-cyan-500/20 bg-cyan-500/5 px-3 py-2 text-[11px] text-slate-300">
              Only the Strategy B controls shown below are applied by the current Day Replay.
              Strategy A uses its canonical configured momentum/confirmation contract. Capital sizing,
              max-trades/day, premium-cap selection, and the legacy hard-ADX control are not applied yet.
            </div>

            {/* RVOL Threshold */}
            <div>
              <div className="flex justify-between text-xs mb-1">
                <span className="text-slate-300 font-medium">Strategy B RVOL Threshold</span>
                <span className="font-bold text-amber-400">{rvolThreshold}x</span>
              </div>
              <input
                type="range"
                min="0.8"
                max="2.5"
                step="0.05"
                value={rvolThreshold}
                onChange={(e) => setRvolThreshold(Number(e.target.value))}
                className="w-full h-1.5 bg-slate-800 rounded-lg cursor-pointer accent-amber-400"
              />
            </div>

            {/* Strategy A V3 confirmation contract */}
            <div className="rounded-lg border border-cyan-500/20 bg-cyan-500/5 p-2.5">
              <div className="text-xs text-slate-300 font-medium">Strategy A V3 Confirmation</div>
              <div className="text-[10px] text-slate-400 mt-1">
                Fixed contract: body ≥ 40%, directional close location ≤ 30%, range ≤ 1.50 ATR.
                The legacy confirmation-score slider does not apply to Strategy A V3.
              </div>
            </div>

            {/* Strategy B Confirmation */}
            <div>
              <div className="flex justify-between text-xs mb-1">
                <span className="text-slate-300 font-medium">Strat B Required Confirmation</span>
                <span className="font-bold text-emerald-400">{stratBMinConf} / 6 pts</span>
              </div>
              <input
                type="range"
                min="1"
                max="5"
                step="1"
                value={stratBMinConf}
                onChange={(e) => setStratBMinConf(Number(e.target.value))}
                className="w-full h-1.5 bg-slate-800 rounded-lg cursor-pointer accent-emerald-400"
              />
            </div>

            {/* Box Max Height */}
            <div>
              <div className="flex justify-between text-xs mb-1">
                <span className="text-slate-300 font-medium">Strat B Box Max Height</span>
                <span className="font-bold text-rose-400">{boxMaxHeightAtr} ATR</span>
              </div>
              <input
                type="range"
                min="0.8"
                max="2.5"
                step="0.1"
                value={boxMaxHeightAtr}
                onChange={(e) => setBoxMaxHeightAtr(Number(e.target.value))}
                className="w-full h-1.5 bg-slate-800 rounded-lg cursor-pointer accent-rose-400"
              />
            </div>

          </div>
        )}
      </div>

      {errorMsg && (
        <div className="p-4 rounded-xl bg-rose-500/10 border border-rose-500/30 text-rose-300 text-xs flex items-center gap-2">
          <AlertCircle className="w-4 h-4 text-rose-400 flex-shrink-0" />
          <span>{errorMsg}</span>
        </div>
      )}

      {/* 2. Simulation Results Cockpit */}
      {result && (
        <>
          <div className="bg-slate-900/95 border border-slate-800 rounded-xl p-4">
            <div className="flex flex-wrap items-center gap-x-6 gap-y-2 text-xs">
              <div>
                <span className="text-slate-400">15m bars checked:</span>{" "}
                <span className="font-mono font-bold text-slate-200">{replayDiagnostics?.completed_bar_checks ?? 0}</span>
              </div>
              <div>
                <span className="text-slate-400">Directional checks:</span>{" "}
                <span className="font-mono font-bold text-slate-200">{replayDiagnostics?.directional_evaluations ?? 0}</span>
              </div>
              <div>
                <span className="text-slate-400">Setups created:</span>{" "}
                <span className="font-mono font-bold text-cyan-300">{replayDiagnostics?.setup_count ?? 0}</span>
              </div>
              <div>
                <span className="text-slate-400">Qualified Strategy A signals:</span>{" "}
                <span className="font-mono font-bold text-cyan-300">{replayDiagnostics?.signal_count ?? 0}</span>
              </div>
              <div>
                <span className="text-slate-400">Resolved Strategy A trades:</span>{" "}
                <span className="font-mono font-bold text-slate-200">{replayDiagnostics?.resolved_trade_count ?? 0}</span>
              </div>
              <div>
                <span className="text-slate-400">Futures 15m coverage:</span>{" "}
                <span className={`font-mono font-bold ${(replayDiagnostics?.futures_entry_window_coverage?.coverage_pct ?? 0) >= 100 ? "text-emerald-300" : "text-amber-300"}`}>
                  {replayDiagnostics?.futures_entry_window_coverage?.coverage_pct ?? 0}%
                </span>
              </div>
            </div>
            {(appliedReplayControls.length > 0 || ignoredReplayOverrides.length > 0 || unsupportedRequestControls.length > 0) && (
              <div className="mt-3 rounded-lg border border-slate-700 bg-slate-950/60 px-3 py-2 text-[10px]">
                <div className="font-bold uppercase tracking-wider text-slate-400">Replay control application</div>
                <div className="mt-1 text-emerald-300">
                  Applied: {appliedReplayControls.length > 0
                    ? appliedReplayControls.map(([name, value]) => `${name}=${String(value)}`).join(", ")
                    : "none"}
                </div>
                <div className="mt-1 text-amber-300">
                  Not applied: {[
                    ...ignoredReplayOverrides.map(([name]) => name),
                    ...unsupportedRequestControls.map(([name]) => name),
                  ].join(", ") || "none"}
                </div>
              </div>
            )}
            {gateFunnelEntries.length > 0 && (
              <div className="mt-3">
                <div className="text-[10px] uppercase tracking-wider font-bold text-slate-400 mb-1.5">
                  Strategy A independent rule funnel
                </div>
                <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
                  {gateFunnelEntries.map(([gate, stats]) => (
                    <div key={gate} className="rounded border border-slate-700 bg-slate-950/60 px-2 py-1.5">
                      <div className="text-[10px] uppercase text-slate-400">{gate}</div>
                      <div className="text-xs font-mono font-bold text-cyan-300">
                        {stats.passed ?? 0}/{stats.evaluated ?? 0} · {(stats.pass_pct ?? 0).toFixed(1)}%
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}
            {trendComponentEntries.length > 0 && (
              <div className="mt-3">
                <div className="text-[10px] uppercase tracking-wider font-bold text-slate-400 mb-1.5">
                  Trend-regime component pass rates
                </div>
                <div className="flex flex-wrap gap-1.5">
                  {trendComponentEntries.map(([name, stats]) => (
                    <span key={name} className="px-2 py-1 rounded bg-slate-950 border border-slate-700 text-slate-300 text-[10px] font-mono">
                      {name.replace("trend.", "")}: {stats.passed ?? 0}/{stats.evaluated ?? 0} ({(stats.pass_pct ?? 0).toFixed(1)}%)
                    </span>
                  ))}
                </div>
              </div>
            )}
            {blockerEntries.length > 0 && (
              <div className="mt-3">
                <div className="text-[10px] uppercase tracking-wider font-bold text-slate-400 mb-1.5">
                  Strategy A market-condition blockers
                </div>
                <div className="flex flex-wrap gap-1.5">
                  {blockerEntries.map(([reason, count]) => (
                    <span key={reason} className="px-2 py-1 rounded bg-amber-500/10 border border-amber-500/25 text-amber-300 text-[10px] font-mono">
                      {reason}: {count}
                    </span>
                  ))}
                </div>
              </div>
            )}
            {dataQualityEntries.length > 0 && (
              <div className="mt-3">
                <div className="text-[10px] uppercase tracking-wider font-bold text-slate-400 mb-1.5">
                  Historical data quality
                </div>
                <div className="flex flex-wrap gap-1.5">
                  {dataQualityEntries.map(([reason, count]) => (
                    <span key={reason} className="px-2 py-1 rounded bg-rose-500/10 border border-rose-500/25 text-rose-300 text-[10px] font-mono">
                      {reason}: {count}
                    </span>
                  ))}
                </div>
                {(replayDiagnostics?.futures_entry_window_coverage?.missing_15m_bar_ends_ist?.length ?? 0) > 0 && (
                  <div className="text-[10px] text-slate-500 mt-1">
                    Missing 15m bar ends: {replayDiagnostics?.futures_entry_window_coverage?.missing_15m_bar_ends_ist?.join(", ")}
                  </div>
                )}
              </div>
            )}
            {(replayData?.missing_data?.length ?? 0) > 0 && (
              <div className="mt-2 text-[11px] text-rose-300">
                Missing replay data: {replayData?.missing_data?.join(", ")}
              </div>
            )}
          </div>

          {/* Performance Summary Cards */}
          <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
            {/* Total Trades */}
            <div className="bg-slate-900/90 border border-slate-800 rounded-xl p-3.5">
              <div className="text-[10px] text-slate-400 uppercase tracking-wider font-semibold mb-1">
                Total Trades
              </div>
              <div className="text-xl font-mono font-bold text-slate-100">
                {trades.length}
              </div>
              <div className="text-[10px] text-slate-400 mt-1">
                {result.winning_trades}W - {result.losing_trades}L
              </div>
            </div>

            {/* Win Rate */}
            <div className="bg-slate-900/90 border border-slate-800 rounded-xl p-3.5">
              <div className="text-[10px] text-slate-400 uppercase tracking-wider font-semibold mb-1">
                Underlying Win Rate
              </div>
              <div
                className={`text-xl font-mono font-bold ${
                  result.win_rate_pct >= 50.0 ? "text-emerald-400" : "text-amber-400"
                }`}
              >
                {trades.length > 0 ? `${result.win_rate_pct}%` : "N/A"}
              </div>
              <div className="text-[10px] text-slate-400 mt-1">
                R Profit Factor: <span className="font-bold text-slate-200">{trades.length > 0 && result.profit_factor != null ? result.profit_factor : "N/A"}</span>
              </div>
            </div>

            {/* Total PnL */}
            <div className="bg-slate-900/90 border border-slate-800 rounded-xl p-3.5">
              <div className="text-[10px] text-slate-400 uppercase tracking-wider font-semibold mb-1">
                Historical Option Mark P&L
              </div>
              <div className={`text-xl font-mono font-bold ${!hasNetPnl ? "text-slate-300" : netPnl >= 0 ? "text-emerald-400" : "text-rose-400"}`}>
                {formatPnl(netPnl)}
              </div>
              <div className="text-[10px] text-slate-400 mt-1">
                Gross mark: {formatAmount(grossPnl)}
              </div>
              <div className="text-[10px] text-amber-300 mt-1">
                {hasNetPnl
                  ? "Completed-candle close marks with estimated costs; not executable fills."
                  : "Historical option marks unavailable."}
              </div>
            </div>
            {/*
              <div className="text-[10px] text-slate-400 uppercase tracking-wider font-semibold mb-1">
                Simulated Net PnL
              </div>
              <div
                className={`text-xl font-mono font-bold ${
                  !hasNetPnl ? "text-slate-300" : netPnl >= 0 ? "text-emerald-400" : "text-rose-400"
                }`}
              >
                {result.net_pnl >= 0 ? "+" : ""}₹{result.net_pnl.toLocaleString()}
              </div>
              <div className="text-[10px] text-slate-400 mt-1">
                Gross: ₹{result.total_pnl.toLocaleString()}
              </div>
            </div>

            */}

            {/* Realized R */}
            <div className="bg-slate-900/90 border border-slate-800 rounded-xl p-3.5">
              <div className="text-[10px] text-slate-400 uppercase tracking-wider font-semibold mb-1">
                Underlying Realized R
              </div>
              <div
                className={`text-xl font-mono font-bold ${
                  result.total_realized_r >= 0 ? "text-cyan-400" : "text-rose-400"
                }`}
              >
                {result.total_realized_r >= 0 ? "+" : ""}
                {result.total_realized_r}R
              </div>
              <div className="text-[10px] text-slate-400 mt-1">
                Avg: {trades.length > 0 ? (result.total_realized_r / trades.length).toFixed(2) : "0.00"}R / trade
              </div>
            </div>

            {/* Underlying R Drawdown */}
            <div className="bg-slate-900/90 border border-slate-800 rounded-xl p-3.5">
              <div className="text-[10px] text-slate-400 uppercase tracking-wider font-semibold mb-1">
                Underlying R Drawdown
              </div>
              <div className="text-xl font-mono font-bold text-slate-300">
                {result.max_drawdown_r == null ? "N/A" : `${result.max_drawdown_r}R`}
              </div>
              <div className="text-[10px] text-slate-400 mt-1">
                Realized lifecycle peak-to-trough
              </div>
            </div>
            {/*
              <div className="text-[10px] text-slate-400 uppercase tracking-wider font-semibold mb-1">
                Max Drawdown
              </div>
              <div className="text-xl font-mono font-bold text-rose-400">
                ₹{result.max_drawdown_pnl.toLocaleString()}
              </div>
              <div className="text-[10px] text-slate-400 mt-1">
                Intraday peak-to-trough
              </div>
            </div>

            */}

            {/* Bars Evaluated */}
            <div className="bg-slate-900/90 border border-slate-800 rounded-xl p-3.5">
              <div className="text-[10px] text-slate-400 uppercase tracking-wider font-semibold mb-1">
                Session Bars
              </div>
              <div className="text-xl font-mono font-bold text-slate-300">
                {result.total_bars_evaluated}
              </div>
              <div className="text-[10px] text-slate-400 mt-1">
                09:15 to 15:30 IST
              </div>
            </div>
          </div>

          {/* 3. Simulated Trades Table */}
          <div className="bg-slate-900/95 border border-slate-800 rounded-xl overflow-hidden shadow-xl">
            <div className="bg-slate-950 px-5 py-3.5 border-b border-slate-800 flex items-center justify-between">
              <h3 className="text-xs font-bold text-slate-200 uppercase tracking-wider flex items-center gap-2">
                Resolved Replay Lifecycles ({trades.length})
              </h3>
              <span className="text-[11px] text-slate-400">
                Underlying lifecycle replay; option ₹ figures are historical close marks with one-lot reconstruction when available.
              </span>
            </div>

            {trades.length === 0 ? (
              <div className="p-8 text-center text-slate-400 text-xs">
                <AlertCircle className="w-6 h-6 text-amber-400 mx-auto mb-2" />
                <span>
                  {(replayDiagnostics?.signal_count ?? result.replay_manifests?.length ?? 0) > 0
                    ? "Signals were identified, but none produced a resolved trade lifecycle with the available historical bars."
                    : "No strategy signal qualified during this session."}
                </span>
                <p className="text-[11px] text-slate-500 mt-1">
                  Review the blocker counts above before changing thresholds; a zero-trade session can be a valid strategy outcome.
                </p>
              </div>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-left text-xs">
                  <thead>
                    <tr className="text-[10px] text-slate-400 uppercase tracking-wider border-b border-slate-800 bg-slate-950/40">
                      <th className="py-2.5 px-4 font-medium">Trade ID</th>
                      <th className="py-2.5 px-3 font-medium">Strategy</th>
                      <th className="py-2.5 px-3 font-medium">Contract</th>
                      <th className="py-2.5 px-3 font-medium">Entry</th>
                      <th className="py-2.5 px-3 font-medium">Exit</th>
                      <th className="py-2.5 px-3 font-medium">Exit Reason</th>
                      <th className="py-2.5 px-3 font-medium text-right">Realized R</th>
                      <th className="py-2.5 px-4 font-medium text-right">Net Mark P&L</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-800/50 font-mono">
                    {trades.map((t) => {
                      const tradeNetPnl = t.net_pnl;
                      const hasTradeNetPnl = tradeNetPnl !== null && tradeNetPnl !== undefined;
                      const isWin = hasTradeNetPnl && tradeNetPnl > 0;
                      return (
                        <tr key={t.trade_id} className="hover:bg-slate-800/30 transition">
                          <td className="py-3 px-4 text-slate-400 text-[11px]">
                            {t.trade_id}
                          </td>
                          <td className="py-3 px-3">
                            <span
                              className={`px-2 py-0.5 rounded text-[10px] font-sans font-bold ${
                                t.strategy === "TREND_PULLBACK"
                                  ? "bg-blue-500/10 text-blue-300 border border-blue-500/30"
                                  : "bg-purple-500/10 text-purple-300 border border-purple-500/30"
                              }`}
                            >
                              {t.strategy === "TREND_PULLBACK" ? "STRAT A" : "STRAT B"}
                            </span>
                          </td>
                          <td className="py-3 px-3">
                            <div className="flex items-center gap-1.5 font-bold text-slate-200">
                              <span
                                className={`px-1.5 py-0.2 rounded text-[10px] ${
                                  t.direction === "BULLISH"
                                    ? "bg-emerald-500/20 text-emerald-300"
                                    : "bg-rose-500/20 text-rose-300"
                                }`}
                              >
                                {t.option_type}
                              </span>
                              <span>{t.contract_symbol}</span>
                            </div>
                            <div className="text-[10px] text-slate-400 font-sans">
                              {t.lots} lots ({t.quantity} qty)
                            </div>
                          </td>
                          <td className="py-3 px-3 text-slate-300">
                            <div>{t.entry_time}</div>
                            <div className="text-[10px] text-slate-400">
                              Option mark: {formatAmount(t.entry_premium)} (Spot: {formatAmount(t.entry_spot)})
                            </div>
                            <div className="hidden">
                              ₹{t.entry_premium} (Spot ₹{t.entry_spot})
                            </div>
                          </td>
                          <td className="py-3 px-3 text-slate-300">
                            <div>{t.exit_time || "-"}</div>
                            <div className="text-[10px] text-slate-400">
                              Option mark: {formatAmount(t.exit_premium)} (Spot: {formatAmount(t.exit_spot)})
                            </div>
                            <div className="hidden">
                              ₹{t.exit_premium ?? "-"} (Spot ₹{t.exit_spot ?? "-"})
                            </div>
                          </td>
                          <td className="py-3 px-3 font-sans text-slate-300 text-[11px]">
                            <span
                              className={`px-2 py-0.5 rounded text-[10px] font-semibold ${
                                t.exit_reason?.includes("PROFIT") || t.exit_reason?.includes("RUNNER")
                                  ? "bg-emerald-500/10 text-emerald-300 border border-emerald-500/30"
                                  : t.exit_reason?.includes("FALSE_BREAKOUT")
                                  ? "bg-amber-500/10 text-amber-300 border border-amber-500/30"
                                  : "bg-rose-500/10 text-rose-300 border border-rose-500/30"
                              }`}
                            >
                              {t.exit_reason || "SESSION_CLOSE"}
                            </span>
                            <div className="text-[10px] text-slate-400 mt-0.5">
                              Held: {t.hold_duration_mins}m | Peak: {t.peak_r}R
                            </div>
                          </td>
                          <td
                            className={`py-3 px-3 text-right font-bold ${
                              t.realized_r >= 0 ? "text-cyan-400" : "text-rose-400"
                            }`}
                          >
                            {t.realized_r >= 0 ? "+" : ""}
                            {t.realized_r}R
                          </td>
                          <td
                            className={`py-3 px-4 text-right font-bold text-sm ${
                              !hasTradeNetPnl ? "text-slate-300" : isWin ? "text-emerald-400" : "text-rose-400"
                            }`}
                          >
                            {hasTradeNetPnl ? formatPnl(tradeNetPnl) : "N/A"}
                          </td>
                          {/*
                          <td
                            className={`py-3 px-3 text-right font-bold ${
                              t.realized_r >= 0 ? "text-cyan-400" : "text-rose-400"
                            }`}
                          >
                            {t.realized_r >= 0 ? "+" : ""}
                            {t.realized_r}R
                          </td>
                          <td
                            className={`py-3 px-4 text-right font-bold text-sm ${
                              isWin ? "text-emerald-400" : "text-rose-400"
                            }`}
                          >
                            {isWin ? "+" : ""}₹{t.net_pnl.toLocaleString()}
                          </td>
                          */}
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </div>

          {/* 4. Interactive Bar-by-Bar Day Replay Stepper */}
          {result.timeline.length > 0 && currentBar && (
            <div className="bg-slate-900/95 border border-slate-800 rounded-xl p-5 shadow-xl space-y-4">
              <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-slate-800 pb-3">
                <div>
                  <h3 className="text-xs font-bold text-slate-200 uppercase tracking-wider flex items-center gap-2">
                    <Activity className="w-4 h-4 text-cyan-400" />
                    Interactive Bar-by-Bar Timeline Stepper
                  </h3>
                  <p className="text-[11px] text-slate-400 mt-0.5">
                    Scrub through the 75 completed 5-minute candles to inspect strategy diagnostic states at every bar.
                  </p>
                </div>

                <div className="flex items-center gap-2">
                  <button
                    onClick={() => setSelectedBarIndex(Math.max(0, selectedBarIndex - 1))}
                    disabled={selectedBarIndex === 0}
                    className="p-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 disabled:opacity-30 text-slate-200 border border-slate-700 transition"
                    title="Previous candle"
                  >
                    <ChevronLeft className="w-4 h-4" />
                  </button>

                  <span className="text-xs font-mono font-bold text-slate-300 px-2">
                    Bar {selectedBarIndex + 1} / {result.timeline.length} ({currentBar.ist_time} IST)
                  </span>

                  <button
                    onClick={() =>
                      setSelectedBarIndex(Math.min(result.timeline.length - 1, selectedBarIndex + 1))
                    }
                    disabled={selectedBarIndex === result.timeline.length - 1}
                    className="p-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 disabled:opacity-30 text-slate-200 border border-slate-700 transition"
                    title="Next candle"
                  >
                    <ChevronRight className="w-4 h-4" />
                  </button>

                  <button
                    onClick={jumpToNextEvent}
                    className="px-2.5 py-1.5 rounded-lg bg-indigo-500/10 hover:bg-indigo-500/20 text-indigo-300 border border-indigo-500/30 text-xs font-semibold transition flex items-center gap-1"
                    title="Jump to next entry or exit event"
                  >
                    <Zap className="w-3 h-3" />
                    <span>Jump to Trade</span>
                  </button>
                </div>
              </div>

              {/* Slider Scrubber */}
              <div>
                <input
                  type="range"
                  min="0"
                  max={result.timeline.length - 1}
                  value={selectedBarIndex}
                  onChange={(e) => setSelectedBarIndex(Number(e.target.value))}
                  className="w-full h-2 bg-slate-800 rounded-lg appearance-none cursor-pointer accent-indigo-400"
                />
                <div className="flex justify-between text-[10px] font-mono text-slate-500 mt-1">
                  <span>09:15 IST (Market Open)</span>
                  <span>12:00 IST (Mid-Day)</span>
                  <span>15:30 IST (Market Close)</span>
                </div>
              </div>

              {/* Selected Bar Diagnostic Snapshot Card */}
              <div className="bg-slate-950/80 p-4 rounded-xl border border-slate-800 grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4 text-xs">
                {/* Candlestick OHLC & Spot */}
                <div>
                  <div className="text-[10px] text-slate-400 uppercase font-bold mb-1">
                    Candle ({currentBar.ist_time} IST)
                  </div>
                  <div className="text-base font-mono font-bold text-slate-100">
                    Spot: ₹{currentBar.spot.toFixed(2)}
                  </div>
                  <div className="text-[11px] font-mono text-slate-400 mt-1">
                    O: {currentBar.open} | H: {currentBar.high} | L: {currentBar.low} | C: {currentBar.close}
                  </div>
                  <div className="text-[10px] text-slate-500 mt-0.5">
                    Volume: {currentBar.volume.toLocaleString()}
                  </div>
                </div>

                {/* Technical Indicators */}
                <div>
                  <div className="text-[10px] text-slate-400 uppercase font-bold mb-1">
                    Indicators at this Bar
                  </div>
                  <div className="space-y-0.5 font-mono text-[11px]">
                    <div>
                      EMA 9/20: <span className="text-cyan-300">{currentBar.ema9_5m}</span> /{" "}
                      <span className="text-slate-300">{currentBar.ema20_5m}</span>
                    </div>
                    <div>
                      Supertrend:{" "}
                      <span
                        className={`font-bold ${
                          currentBar.supertrend === "BULLISH" ? "text-emerald-400" : "text-rose-400"
                        }`}
                      >
                        {currentBar.supertrend}
                      </span>
                    </div>
                    <div>
                      ADX: <span className="text-indigo-300">{currentBar.adx_15m}</span> | RVOL:{" "}
                      <span className="text-amber-300">{currentBar.rvol_5m}x</span>
                    </div>
                    <div>
                      BB Width %ile: <span className="text-purple-300">{currentBar.bb_width_percentile}%</span>
                    </div>
                  </div>
                </div>

                {/* Strategy Phase States */}
                <div>
                  <div className="text-[10px] text-slate-400 uppercase font-bold mb-1">
                    Strategy Phase States
                  </div>
                  <div className="space-y-1.5 font-sans">
                    <div>
                      <span className="text-[10px] text-slate-400 block">Strat A (Trend Pullback):</span>
                      <span className="px-2 py-0.5 rounded text-[10px] font-bold bg-blue-500/10 text-blue-300 border border-blue-500/30">
                        {currentBar.strategy_a_phase}
                      </span>
                    </div>
                    <div>
                      <span className="text-[10px] text-slate-400 block">Strat B (Vol Breakout):</span>
                      <span className="px-2 py-0.5 rounded text-[10px] font-bold bg-purple-500/10 text-purple-300 border border-purple-500/30">
                        {currentBar.strategy_b_phase}
                      </span>
                    </div>
                  </div>
                </div>

                {/* Event & Trade Status */}
                <div>
                  <div className="text-[10px] text-slate-400 uppercase font-bold mb-1">
                    Trade Lifecycle at this Bar
                  </div>
                  {currentBar.event ? (
                    <div className="p-2.5 rounded-lg bg-indigo-500/10 border border-indigo-500/30 text-indigo-300">
                      <div className="font-bold flex items-center gap-1.5">
                        <Flame className="w-3.5 h-3.5 text-amber-400" />
                        <span>{currentBar.event}</span>
                      </div>
                      <div className="text-[10px] text-slate-300 mt-1">
                        {currentBar.event_details}
                      </div>
                    </div>
                  ) : currentBar.active_trade_id ? (
                    <div className="p-2 rounded-lg bg-emerald-500/10 border border-emerald-500/30 text-emerald-300">
                      <div className="font-bold">IN ACTIVE POSITION</div>
                      <div className="text-[10px] text-slate-300 mt-0.5">
                        Trade: {currentBar.active_trade_id}
                      </div>
                      {currentBar.event_details && (
                        <div className="text-[10px] text-cyan-300 mt-0.5">
                          {currentBar.event_details}
                        </div>
                      )}
                    </div>
                  ) : (
                    <div className="text-slate-500 italic">
                      No active trade. Monitoring conditions for entry.
                    </div>
                  )}
                </div>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
};
