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
  ReplayComparisonData,
  ReplayRunSummaryData,
  SimulatedTradeRecordData,
  SimulationBarSnapshotData,
  SimulationResultData,
  compareReplayRuns,
  fetchReplayRuns,
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
  const [replayRuns, setReplayRuns] = useState<ReplayRunSummaryData[]>([]);
  const [comparisonBaselineId, setComparisonBaselineId] = useState<string>("");
  const [comparison, setComparison] = useState<ReplayComparisonData | null>(null);
  const [isComparing, setIsComparing] = useState<boolean>(false);

  // Timeline scrubber state
  const [selectedBarIndex, setSelectedBarIndex] = useState<number>(0);

  // Overrides panel state
  const [showOverrides, setShowOverrides] = useState<boolean>(false);
  const [rvolThreshold, setRvolThreshold] = useState<number>(1.20);
  const [stratBMinConf, setStratBMinConf] = useState<number>(3);
  const [boxMaxHeightAtr, setBoxMaxHeightAtr] = useState<number>(1.30);
  const [bypassWindow, setBypassWindow] = useState<boolean>(false);
  const [replayMode, setReplayMode] = useState<"RESEARCH" | "EXECUTION_PARITY">("RESEARCH");
  const [selectedStrategy, setSelectedStrategy] = useState<
    "ALL" | "TREND_PULLBACK" | "VOLATILITY_BREAKOUT" | "DI_CONTINUATION" |
    "SR_MOMENTUM_BREAKOUT" | "PIVOT_VWAP_SCALP"
  >("ALL");
  const [replayCapital, setReplayCapital] = useState<number>(500000);
  const [replayRiskPct, setReplayRiskPct] = useState<number>(0.5);
  const [replayMaxTrades, setReplayMaxTrades] = useState<number>(5);

  useEffect(() => {
    setResult(null);
    setComparison(null);
    loadDates();
    loadReplayRuns();
  }, [historicalSource]);

  const loadReplayRuns = async (candidateRunId?: string) => {
    try {
      const runs = await fetchReplayRuns(30);
      setReplayRuns(runs);
      setComparisonBaselineId((current) => {
        if (
          current
          && current !== candidateRunId
          && runs.some((run) => run.run_id === current)
        ) {
          return current;
        }
        return runs.find((run) => run.run_id !== candidateRunId)?.run_id || "";
      });
    } catch (err) {
      console.error("Failed to load replay run history", err);
    }
  };

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
        replay_mode: replayMode,
        ...(selectedStrategy === "ALL"
          ? {}
          : { selected_strategies: [selectedStrategy] }),
        ...(replayMode === "EXECUTION_PARITY"
          ? {
              capital: Number(replayCapital),
              risk_per_trade_pct: Number(replayRiskPct),
              max_trades_per_day: Number(replayMaxTrades),
            }
          : {}),
      });
      setResult(res);
      setComparison(null);
      setSelectedBarIndex(0);
      await loadReplayRuns(res.run_id || undefined);
    } catch (err: any) {
      setErrorMsg(err.message || "Simulation failed to run.");
    } finally {
      setIsRunning(false);
    }
  };

  const handleCompareRuns = async () => {
    if (
      !comparisonBaselineId
      || !result?.run_id
      || comparisonBaselineId === result.run_id
    ) {
      return;
    }
    setIsComparing(true);
    setErrorMsg(null);
    try {
      setComparison(
        await compareReplayRuns(comparisonBaselineId, result.run_id),
      );
    } catch (err: any) {
      setErrorMsg(err.message || "Replay comparison failed.");
    } finally {
      setIsComparing(false);
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
  const executionNetPnl = result?.option_mark_metrics?.net_estimated_executable_pnl;
  const executionGrossPnl = result?.option_mark_metrics?.gross_estimated_executable_pnl;
  const executionSlippage = result?.option_mark_metrics?.estimated_slippage_costs;
  const executionCosts = result?.option_mark_metrics?.estimated_execution_transaction_costs;
  const hasExecutionPnl = executionNetPnl !== null && executionNetPnl !== undefined;
  const portfolio = result?.portfolio_metrics;
  const reproducibility = result?.reproducibility || {};
  const comparisonMetrics = Object.entries(comparison?.metrics || {});
  const comparisonStrategyRows = Object.entries(
    comparison?.strategy_realized_r || {},
  );
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
        applied_request_controls?: Record<string, unknown>;
        conditionally_applied_overrides?: Record<string, { value?: unknown; reason?: string }>;
        not_applied_overrides?: Record<string, { value?: unknown; reason?: string }>;
        not_applied_request_controls?: Record<string, { value?: unknown; reason?: string }>;
      }
    | undefined;
  const appliedReplayControls = [
    ...Object.entries(controlApplication?.applied_overrides || {}),
    ...Object.entries(controlApplication?.applied_request_controls || {}),
  ];
  const conditionalReplayControls = Object.entries(controlApplication?.conditionally_applied_overrides || {});
  const ignoredReplayOverrides = Object.entries(controlApplication?.not_applied_overrides || {});
  const unsupportedRequestControls = Object.entries(controlApplication?.not_applied_request_controls || {});
  const strategyReplaySummary = (result?.replay_metadata?.strategy_replay_summary || {}) as Record<
    string,
    {
      display_name?: string;
      priority?: number;
      enabled?: boolean;
      signals?: number;
      resolved?: number;
      unresolved?: number;
      ambiguous?: number;
      total_realized_r?: number;
    }
  >;
  const strategySummaryEntries = Object.entries(strategyReplaySummary).sort(
    ([, left], [, right]) => (left.priority ?? 999) - (right.priority ?? 999),
  );

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
      <div className="text-[11px] text-slate-400 flex flex-wrap gap-x-4 gap-y-1">
        <span>
          Historical source: <span className="font-mono font-bold text-cyan-300">{historicalSource}</span>
        </span>
        <span>
          Replay mode: <span className="font-mono font-bold text-indigo-300">{(result?.replay_metadata?.requested_replay_mode as string | undefined) || replayMode}</span>
        </span>
        {result?.run_id && (
          <span>
            Run: <span className="font-mono font-bold text-slate-200">{result.run_id}</span>
          </span>
        )}
        {reproducibility.configuration_fingerprint && (
          <span title={String(reproducibility.configuration_fingerprint)}>
            Config: <span className="font-mono text-slate-300">{String(reproducibility.configuration_fingerprint).slice(0, 10)}</span>
          </span>
        )}
        {reproducibility.data_fingerprint && (
          <span title={String(reproducibility.data_fingerprint)}>
            Data: <span className="font-mono text-slate-300">{String(reproducibility.data_fingerprint).slice(0, 10)}</span>
          </span>
        )}
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

        <div className="mt-3 flex flex-col gap-2 rounded-lg border border-slate-800 bg-slate-950/50 p-3">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-xs font-semibold text-slate-300 mr-1">Replay semantics:</span>
            {(["RESEARCH", "EXECUTION_PARITY"] as const).map((mode) => (
              <button
                key={mode}
                onClick={() => {
                  setReplayMode(mode);
                  setResult(null);
                }}
                className={`px-3 py-1.5 rounded-md border text-xs font-semibold transition ${
                  replayMode === mode
                    ? "bg-indigo-500/20 border-indigo-500/50 text-indigo-300"
                    : "bg-slate-900 border-slate-700 text-slate-400 hover:text-slate-200"
                }`}
              >
                {mode === "RESEARCH" ? "Research / Signal Replay" : "Execution Parity"}
              </button>
            ))}
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <label htmlFor="replay-strategy" className="text-xs font-semibold text-slate-300 mr-1">
              Strategy:
            </label>
            <select
              id="replay-strategy"
              value={selectedStrategy}
              onChange={(e) => {
                setSelectedStrategy(e.target.value as typeof selectedStrategy);
                setResult(null);
              }}
              className="rounded-md border border-slate-700 bg-slate-900 px-2 py-1.5 text-xs text-slate-200"
            >
              <option value="ALL">All enabled strategies</option>
              <option value="TREND_PULLBACK">Strategy A · Trend Pullback R5</option>
              <option value="VOLATILITY_BREAKOUT">Strategy B · Volatility Breakout</option>
              <option value="DI_CONTINUATION">Strategy C · DI Continuation (Frozen)</option>
              <option value="SR_MOMENTUM_BREAKOUT">Strategy D · S&amp;R Momentum</option>
              <option value="PIVOT_VWAP_SCALP">Strategy E · Pivot/VWAP Scalp</option>
            </select>
            {selectedStrategy === "DI_CONTINUATION" && (
              <span className="text-[10px] text-cyan-300">
                Frozen candidate thresholds/fingerprint; selection changes orchestration only.
              </span>
            )}
          </div>
          <div className="text-[10px] leading-relaxed text-slate-400">
            {replayMode === "RESEARCH"
              ? "Discovers qualified A/B/C/D/E signals independently, then resolves each strategy with its own production or frozen lifecycle authority. Useful for hypothesis analysis."
              : "Walks forward chronologically with production numeric risk gates and sizing. Exact stored point-in-time chain snapshots rerun the production ContractSelector; other dates are explicitly APPROXIMATED_SELECTION. Historical marks remain separate from estimated fills."}
          </div>
        </div>

        {/* Expandable What-If Overrides Panel */}
        {showOverrides && (
          <div className="mt-4 pt-4 border-t border-slate-800 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3 bg-slate-950/60 p-4 rounded-lg border border-slate-800/80">
            <div className="sm:col-span-2 lg:col-span-3 rounded-lg border border-cyan-500/20 bg-cyan-500/5 px-3 py-2 text-[11px] text-slate-300">
              Strategy B signal overrides apply in both modes. In Execution Parity, replay capital,
              risk-per-trade %, daily trade limits, concurrent-position limits, cooldown, daily-R loss
              limits, and per-strategy limits are applied. Strategy A keeps its canonical signal contract.
              Premium-cap selection is conditional on exact point-in-time chain evidence; approximated selection cannot prove liquidity/premium gates. Strategy C/D frozen candidate thresholds and Strategy E production lifecycle rules are not what-if tuned here. The legacy hard-ADX control remains unapplied.
            </div>

            {replayMode === "EXECUTION_PARITY" && (
              <>
                <div>
                  <div className="text-xs text-slate-300 font-medium mb-1">Replay Capital</div>
                  <input
                    type="number"
                    min="50000"
                    step="50000"
                    value={replayCapital}
                    onChange={(e) => setReplayCapital(Number(e.target.value))}
                    className="w-full rounded-md border border-slate-700 bg-slate-900 px-2 py-1.5 text-xs text-slate-200"
                  />
                  <div className="mt-1 text-[10px] text-slate-500">Account equity used by production sizing math.</div>
                </div>
                <div>
                  <div className="text-xs text-slate-300 font-medium mb-1">Risk / Trade (%)</div>
                  <input
                    type="number"
                    min="0.1"
                    max="5"
                    step="0.1"
                    value={replayRiskPct}
                    onChange={(e) => setReplayRiskPct(Number(e.target.value))}
                    className="w-full rounded-md border border-slate-700 bg-slate-900 px-2 py-1.5 text-xs text-slate-200"
                  />
                  <div className="mt-1 text-[10px] text-slate-500">Overrides the replay risk budget only.</div>
                </div>
                <div>
                  <div className="text-xs text-slate-300 font-medium mb-1">Max Trades / Day</div>
                  <input
                    type="number"
                    min="1"
                    max="20"
                    step="1"
                    value={replayMaxTrades}
                    onChange={(e) => setReplayMaxTrades(Number(e.target.value))}
                    className="w-full rounded-md border border-slate-700 bg-slate-900 px-2 py-1.5 text-xs text-slate-200"
                  />
                  <div className="mt-1 text-[10px] text-slate-500">Chronological accepted-entry limit.</div>
                </div>
              </>
            )}

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

            {/* Strategy A R5 confirmation contract */}
            <div className="rounded-lg border border-cyan-500/20 bg-cyan-500/5 p-2.5">
              <div className="text-xs text-slate-300 font-medium">Strategy A R5 Confirmation</div>
              <div className="text-[10px] text-slate-400 mt-1">
                Fixed contract: body ≥ 40%, directional close location ≤ 30%, range ≤ 1.50 ATR.
                The legacy confirmation-score slider does not apply to Strategy A R5.
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
            {strategySummaryEntries.length > 0 && (
              <div className="mt-3">
                <div className="text-[10px] uppercase tracking-wider font-bold text-slate-400 mb-1.5">
                  Five-strategy replay summary
                </div>
                <div className="grid grid-cols-2 md:grid-cols-5 gap-2">
                  {strategySummaryEntries.map(([strategy, stats]) => (
                    <div key={strategy} className="rounded border border-slate-700 bg-slate-950/60 px-2 py-1.5">
                      <div className="text-[9px] uppercase text-slate-500 truncate" title={stats.display_name || strategy}>
                        {strategy}
                      </div>
                      <div className="text-xs font-mono font-bold text-cyan-300">
                        {stats.resolved ?? 0}/{stats.signals ?? 0} resolved
                      </div>
                      <div className="text-[9px] text-slate-400">
                        R: {(stats.total_realized_r ?? 0).toFixed(2)}
                        {stats.enabled === false ? " · disabled" : ""}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}
            {(appliedReplayControls.length > 0 || conditionalReplayControls.length > 0 || ignoredReplayOverrides.length > 0 || unsupportedRequestControls.length > 0) && (
              <div className="mt-3 rounded-lg border border-slate-700 bg-slate-950/60 px-3 py-2 text-[10px]">
                <div className="font-bold uppercase tracking-wider text-slate-400">Replay control application</div>
                <div className="mt-1 text-emerald-300">
                  Applied: {appliedReplayControls.length > 0
                    ? appliedReplayControls.map(([name, value]) => `${name}=${String(value)}`).join(", ")
                    : "none"}
                </div>
                {conditionalReplayControls.length > 0 && (
                  <div className="mt-1 text-cyan-300">
                    Conditional: {conditionalReplayControls.map(([name]) => name).join(", ")}
                  </div>
                )}
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
            {replayMode === "EXECUTION_PARITY" && (
              <div className="bg-slate-900/90 border border-slate-800 rounded-xl p-3.5">
                <div className="text-[10px] text-slate-400 uppercase tracking-wider font-semibold mb-1">
                  Estimated Executable P&L
                </div>
                <div className={`text-xl font-mono font-bold ${!hasExecutionPnl ? "text-slate-300" : (executionNetPnl ?? 0) >= 0 ? "text-emerald-400" : "text-rose-400"}`}>
                  {formatPnl(executionNetPnl)}
                </div>
                <div className="text-[10px] text-slate-400 mt-1">
                  Gross fills: {formatAmount(executionGrossPnl)} · Slippage: {formatAmount(executionSlippage)} · Costs: {formatAmount(executionCosts)}
                </div>
                <div className="text-[10px] text-cyan-300 mt-1">
                  {hasExecutionPnl
                    ? `${result?.option_mark_metrics?.bid_ask_supported_trades ?? 0} trade(s) fully bid/ask-supported; ${result?.option_mark_metrics?.mark_fallback_fill_trades ?? 0} used mark±slippage fallback.`
                    : "Estimated fill economics unavailable for one or more resolved trades."}
                </div>
              </div>
            )}

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

          {replayMode === "EXECUTION_PARITY" && portfolio?.available && (
            <div className="bg-slate-900/95 border border-slate-800 rounded-xl p-5 shadow-xl space-y-4">
              <div className="flex flex-col lg:flex-row lg:items-center justify-between gap-2">
                <div>
                  <h3 className="text-xs font-bold text-slate-200 uppercase tracking-wider">
                    Chronological Portfolio Analytics
                  </h3>
                  <p className="text-[11px] text-slate-400 mt-1">
                    Account metrics use accepted chronological entries and resolved estimated executable exits. P&L metrics fail closed when any resolved trade lacks execution economics.
                  </p>
                </div>
                <div className="text-[10px] font-mono text-slate-400">
                  {portfolio.lifecycle_complete ? "LIFECYCLE COMPLETE" : "LIFECYCLE INCOMPLETE"} · {portfolio.pnl_complete ? "P&L COMPLETE" : "P&L INCOMPLETE"} · {portfolio.resolved_entries}/{portfolio.accepted_entries} resolved
                </div>
              </div>

              <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                <div className="rounded-lg border border-slate-800 bg-slate-950/50 p-3">
                  <div className="text-[9px] uppercase tracking-wider text-slate-500">Ending Equity</div>
                  <div className="mt-1 font-mono font-bold text-slate-200">{formatAmount(portfolio.ending_equity)}</div>
                  <div className="text-[10px] text-slate-500">Start {formatAmount(portfolio.starting_equity)}</div>
                </div>
                <div className="rounded-lg border border-slate-800 bg-slate-950/50 p-3">
                  <div className="text-[9px] uppercase tracking-wider text-slate-500">Portfolio Drawdown</div>
                  <div className="mt-1 font-mono font-bold text-slate-200">{formatAmount(portfolio.max_drawdown_pnl)}</div>
                  <div className="text-[10px] text-slate-500">
                    {portfolio.max_drawdown_pct == null ? "N/A" : String(portfolio.max_drawdown_pct) + "%"} · {portfolio.max_drawdown_r == null ? "N/A" : String(portfolio.max_drawdown_r) + "R"}
                  </div>
                </div>
                <div className="rounded-lg border border-slate-800 bg-slate-950/50 p-3">
                  <div className="text-[9px] uppercase tracking-wider text-slate-500">Expectancy</div>
                  <div className="mt-1 font-mono font-bold text-slate-200">{formatPnl(portfolio.expectancy_pnl)}</div>
                  <div className="text-[10px] text-slate-500">{portfolio.expectancy_r == null ? "N/A" : String(portfolio.expectancy_r) + "R / trade"} · PF {portfolio.profit_factor_pnl ?? "N/A"}</div>
                  <div className="text-[10px] text-slate-500">Streaks: {portfolio.max_consecutive_wins}W / {portfolio.max_consecutive_losses}L</div>
                </div>
                <div className="rounded-lg border border-slate-800 bg-slate-950/50 p-3">
                  <div className="text-[9px] uppercase tracking-wider text-slate-500">Exposure & Utilization</div>
                  <div className="mt-1 font-mono font-bold text-slate-200">{portfolio.exposure_pct}%</div>
                  <div className="text-[10px] text-slate-500">
                    {portfolio.exposure_minutes}m · max {portfolio.max_concurrent_positions} concurrent · avg capital {portfolio.average_premium_utilization_pct}%
                  </div>
                </div>
              </div>

              <div className="grid grid-cols-1 lg:grid-cols-3 gap-3 text-[11px]">
                <div className="rounded-lg border border-slate-800 bg-slate-950/40 p-3">
                  <div className="text-[9px] uppercase tracking-wider text-slate-500 mb-1">Capital / Risk Budget</div>
                  <div className="text-slate-300">Peak premium: {formatAmount(portfolio.peak_premium_committed)} ({portfolio.peak_premium_utilization_pct}%)</div>
                  <div className="text-slate-300">Peak risk budget: {formatAmount(portfolio.peak_risk_budget_committed)} ({portfolio.peak_risk_budget_utilization_pct}%)</div>
                </div>
                <div className="rounded-lg border border-slate-800 bg-slate-950/40 p-3">
                  <div className="text-[9px] uppercase tracking-wider text-slate-500 mb-1">Risk Triggers / Rejects</div>
                  <div className="text-slate-300">Rejected opportunities: {portfolio.rejected_opportunities}</div>
                  <div className="text-slate-300">Daily-loss triggers: {portfolio.daily_loss_trigger_events?.length ?? 0}</div>
                  <div className="text-slate-500">
                    {Object.entries(portfolio.risk_gate_block_counts || {}).length > 0
                      ? Object.entries(portfolio.risk_gate_block_counts).map(([name, count]) => name + ": " + count).join(" · ")
                      : "No global risk-gate suppression recorded."}
                  </div>
                </div>
                <div className="rounded-lg border border-slate-800 bg-slate-950/40 p-3">
                  <div className="text-[9px] uppercase tracking-wider text-slate-500 mb-1">Strategy Realized R</div>
                  <div className="text-slate-300">
                    {Object.entries(portfolio.strategy_r_statistics || {}).length > 0
                      ? Object.entries(portfolio.strategy_r_statistics).map(([strategy, stats]) =>
                          strategy + ": " + stats.total_realized_r + "R · E " + stats.expectancy_r +
                          "R · PF " + (stats.profit_factor_r ?? "N/A") + " · DD " + stats.max_drawdown_r + "R"
                        ).join(" | ")
                      : "No resolved strategy R."}
                  </div>
                </div>
              </div>
              {portfolio.limitation && (
                <div className="text-[11px] text-amber-300">{portfolio.limitation}</div>
              )}
            </div>
          )}

          {result?.run_id && (
            <div className="bg-slate-900/95 border border-slate-800 rounded-xl p-5 shadow-xl space-y-3">
              <div>
                <h3 className="text-xs font-bold text-slate-200 uppercase tracking-wider">Replay Reproducibility & Comparison</h3>
                <p className="text-[11px] text-slate-400 mt-1">
                  Compare canonical replay metrics against a persisted run. Deltas are descriptive only; no run is ranked or selected as a winner.
                </p>
              </div>
              <div className="grid grid-cols-1 lg:grid-cols-[1fr_auto] gap-2">
                <select
                  value={comparisonBaselineId}
                  onChange={(event) => {
                    setComparisonBaselineId(event.target.value);
                    setComparison(null);
                  }}
                  className="rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-xs text-slate-200"
                >
                  <option value="">Select baseline run</option>
                  {replayRuns
                    .filter((run) => run.run_id !== result.run_id)
                    .map((run) => (
                      <option key={run.run_id} value={run.run_id}>
                        {run.session_date} · {run.replay_mode} · {run.run_id}
                      </option>
                    ))}
                </select>
                <button
                  onClick={handleCompareRuns}
                  disabled={!comparisonBaselineId || comparisonBaselineId === result.run_id || isComparing}
                  className="rounded-md border border-cyan-500/30 bg-cyan-500/10 px-4 py-2 text-xs font-semibold text-cyan-300 disabled:opacity-40"
                >
                  {isComparing ? "Comparing..." : "Compare with current run"}
                </button>
              </div>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-2 text-[10px] font-mono text-slate-400">
                <div>Current: {result.run_id}</div>
                <div>Cost model: {String(reproducibility.cost_model_version || "N/A")}</div>
                <div>Run fingerprint: {String(reproducibility.run_fingerprint || "N/A")}</div>
                <div>Config fingerprint: {String(reproducibility.configuration_fingerprint || "N/A")}</div>
                <div>Data fingerprint: {String(reproducibility.data_fingerprint || "N/A")}</div>
                <div>Selection policy: {String(reproducibility.contract_selection_policy || "N/A")}</div>
                <div>Historical source: {String(reproducibility.historical_source || historicalSource)}</div>
                <div>Engine revision: {String(reproducibility.replay_engine_revision || "N/A")}</div>
                <div>Strategy manifest: {Object.keys((reproducibility.strategy_manifest || {}) as Record<string, unknown>).length} fingerprinted strategies</div>
                <div>Data provenance: {String((reproducibility.data_provenance as Record<string, any> | undefined)?.dataset_hash || reproducibility.data_fingerprint || "N/A")}</div>
              </div>

              {(reproducibility.contract_selection_evidence || reproducibility.execution_fill_methods) && (
                <div className="grid grid-cols-1 md:grid-cols-2 gap-2 text-[10px] text-slate-400">
                  <div>
                    Selection evidence: {Object.entries((reproducibility.contract_selection_evidence || {}) as Record<string, number>)
                      .map(([name, count]) => name + ": " + count)
                      .join(" · ") || "none"}
                  </div>
                  <div>
                    Fill evidence: {Object.entries((reproducibility.execution_fill_methods || {}) as Record<string, number>)
                      .map(([name, count]) => name + ": " + count)
                      .join(" · ") || "none"}
                  </div>
                </div>
              )}

              {comparison && (
                <div className="space-y-3">
                  {!comparison.configuration_compatible && (
                    <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-[11px] text-amber-200">
                      Configuration/provenance mismatch: {comparison.configuration_mismatches.join(", ")}. Metric deltas are shown for inspection only; these runs are not like-for-like.
                    </div>
                  )}
                  <div className="flex flex-wrap gap-2 text-[10px]">
                    {Object.entries(comparison.identity).map(([name, same]) => (
                      <span key={name} className="rounded border border-slate-700 bg-slate-950/60 px-2 py-1 text-slate-300">
                        {name.replace(/^same_/, "").replaceAll("_", " ")}: {same ? "same" : "different"}
                      </span>
                    ))}
                  </div>
                  <div className="overflow-x-auto rounded-lg border border-slate-800">
                    <table className="w-full text-left text-[11px]">
                      <thead className="bg-slate-950 text-[9px] uppercase tracking-wider text-slate-500">
                        <tr>
                          <th className="px-3 py-2">Metric</th>
                          <th className="px-3 py-2 text-right">Baseline</th>
                          <th className="px-3 py-2 text-right">Current</th>
                          <th className="px-3 py-2 text-right">Delta</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-slate-800 font-mono text-slate-300">
                        {comparisonMetrics.map(([name, metric]) => (
                          <tr key={name}>
                            <td className="px-3 py-2 font-sans">{name.replaceAll("_", " ")}</td>
                            <td className="px-3 py-2 text-right">{metric.baseline ?? "N/A"}</td>
                            <td className="px-3 py-2 text-right">{metric.candidate ?? "N/A"}</td>
                            <td className="px-3 py-2 text-right">{metric.delta == null ? "N/A" : (metric.delta >= 0 ? "+" : "") + metric.delta}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  {comparisonStrategyRows.length > 0 && (
                    <div className="text-[10px] text-slate-400">
                      Strategy R deltas: {comparisonStrategyRows.map(([strategy, metric]) => strategy + " " + (metric.delta == null ? "N/A" : (metric.delta >= 0 ? "+" : "") + metric.delta + "R")).join(" · ")}
                    </div>
                  )}
                </div>
              )}
            </div>
          )}

          {/* 3. Simulated Trades Table */}
          <div className="bg-slate-900/95 border border-slate-800 rounded-xl overflow-hidden shadow-xl">
            <div className="bg-slate-950 px-5 py-3.5 border-b border-slate-800 flex items-center justify-between">
              <h3 className="text-xs font-bold text-slate-200 uppercase tracking-wider flex items-center gap-2">
                Resolved Replay Lifecycles ({trades.length})
              </h3>
              <span className="text-[11px] text-slate-400">
                Underlying lifecycle replay with historical marks and separately estimated execution fills when evidence is available.
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
                      <th className="py-2.5 px-3 font-medium text-right">Net Mark P&L</th>
                      <th className="py-2.5 px-4 font-medium text-right">Est. Exec P&L</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-800/50 font-mono">
                    {trades.map((t) => {
                      const strategyLabel: Record<string, string> = {
                        TREND_PULLBACK: "STRAT A",
                        VOLATILITY_BREAKOUT: "STRAT B",
                        DI_CONTINUATION: "STRAT C",
                        SR_MOMENTUM_BREAKOUT: "STRAT D",
                        PIVOT_VWAP_SCALP: "STRAT E",
                      };
                      const tradeNetPnl = t.net_pnl;
                      const hasTradeNetPnl = tradeNetPnl !== null && tradeNetPnl !== undefined;
                      const isWin = hasTradeNetPnl && tradeNetPnl > 0;
                      const executionTradePnl = t.estimated_executable_net_pnl;
                      const hasExecutionTradePnl = executionTradePnl !== null && executionTradePnl !== undefined;
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
                              {strategyLabel[t.strategy] || t.strategy}
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
                            {t.contract_selection_evidence_status && (
                              <div className="text-[10px] text-cyan-300 font-sans mt-0.5">
                                {t.contract_selection_evidence_status}
                              </div>
                            )}
                          </td>
                          <td className="py-3 px-3 text-slate-300">
                            <div>{t.entry_time}</div>
                            <div className="text-[10px] text-slate-400">
                              Option mark: {formatAmount(t.entry_mark ?? t.entry_premium)} (Spot: {formatAmount(t.entry_spot)})
                            </div>
                            {t.simulated_entry_fill !== null && t.simulated_entry_fill !== undefined && (
                              <div className="text-[10px] text-cyan-300">
                                Est. fill: {formatAmount(t.simulated_entry_fill)}
                              </div>
                            )}
                          </td>
                          <td className="py-3 px-3 text-slate-300">
                            <div>{t.exit_time || "-"}</div>
                            <div className="text-[10px] text-slate-400">
                              Option mark: {formatAmount(t.exit_mark ?? t.exit_premium)} (Spot: {formatAmount(t.exit_spot)})
                            </div>
                            {t.simulated_exit_fill !== null && t.simulated_exit_fill !== undefined && (
                              <div className="text-[10px] text-cyan-300">
                                Est. fill: {formatAmount(t.simulated_exit_fill)}
                              </div>
                            )}
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
                            className={`py-3 px-3 text-right font-bold text-sm ${
                              !hasTradeNetPnl ? "text-slate-300" : isWin ? "text-emerald-400" : "text-rose-400"
                            }`}
                          >
                            {hasTradeNetPnl ? formatPnl(tradeNetPnl) : "N/A"}
                          </td>
                          <td
                            className={`py-3 px-4 text-right font-bold text-sm ${
                              !hasExecutionTradePnl
                                ? "text-slate-300"
                                : (executionTradePnl ?? 0) >= 0
                                ? "text-emerald-400"
                                : "text-rose-400"
                            }`}
                          >
                            {hasExecutionTradePnl
                              ? formatPnl(executionTradePnl)
                              : "N/A"}
                            {t.simulated_entry_fill_method && (
                              <div className="mt-0.5 text-[9px] font-sans font-normal text-slate-500">
                                {t.simulated_entry_fill_method.includes("BID_ASK")
                                  && t.simulated_exit_fill_method?.includes("BID_ASK")
                                  ? "bid/ask evidence"
                                  : "mark fallback"}
                              </div>
                            )}
                            {hasExecutionTradePnl && (
                              <div className="mt-0.5 text-[9px] font-sans font-normal text-slate-500">
                                Slip: {formatAmount(t.estimated_slippage_cost)} · Fees: {formatAmount(t.estimated_transaction_costs)}
                              </div>
                            )}
                          </td>
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
                    <div className="grid grid-cols-3 gap-1">
                      {[
                        ["C", currentBar.strategy_c_phase],
                        ["D", currentBar.strategy_d_phase],
                        ["E", currentBar.strategy_e_phase],
                      ].map(([label, phase]) => (
                        <div key={label}>
                          <span className="text-[9px] text-slate-500 block">Strat {label}</span>
                          <span className="block truncate px-1.5 py-0.5 rounded text-[9px] font-bold bg-slate-800 text-slate-300 border border-slate-700" title={phase || "WAITING"}>
                            {phase || "WAITING"}
                          </span>
                        </div>
                      ))}
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
