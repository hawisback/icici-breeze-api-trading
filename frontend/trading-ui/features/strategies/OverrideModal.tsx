"use client";

import React, { useState, useEffect } from "react";
import {
  AlertTriangle,
  Check,
  Flame,
  HelpCircle,
  RefreshCw,
  RotateCcw,
  Sliders,
  X,
  Zap,
} from "lucide-react";
import {
  ThresholdOverridesData,
  fetchThresholdOverrides,
  fetchStrategyConfig,
  forceStrategyEntry,
  resetThresholdOverrides,
  updateThresholdOverrides,
} from "../../lib/api";

interface OverrideModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSuccess: () => void;
  defaultCap?: number;
}

export const OverrideModal: React.FC<OverrideModalProps> = ({
  isOpen,
  onClose,
  onSuccess,
  defaultCap = 70.0,
}) => {
  const [loading, setLoading] = useState(false);
  const [actionMessage, setActionMessage] = useState<{ type: "success" | "error"; text: string } | null>(null);

  // Overrides form state
  const [premiumCap, setPremiumCap] = useState<number>(defaultCap);
  const [adxThreshold, setAdxThreshold] = useState<number>(22.0);
  const [rvolThreshold, setRvolThreshold] = useState<number>(1.20);
  const [minConfirmationScore, setMinConfirmationScore] = useState<number>(2);
  const [stratBMinConfirmation, setStratBMinConfirmation] = useState<number>(3);
  const [boxMaxHeightAtr, setBoxMaxHeightAtr] = useState<number>(1.30);
  const [bullDerivScore, setBullDerivScore] = useState<number>(2.0);
  const [bearDerivScore, setBearDerivScore] = useState<number>(2.0);
  const [bbWidthPercentile, setBbWidthPercentile] = useState<number>(25.0);
  const [bypassWindow, setBypassWindow] = useState<boolean>(false);

  // Force trigger setup state
  const forceStrategy = "VOLATILITY_BREAKOUT" as const;
  const [forceDirection, setForceDirection] = useState<"BULLISH" | "BEARISH">("BULLISH");
  const [forceLoading, setForceLoading] = useState(false);

  useEffect(() => {
    if (isOpen) {
      loadCurrentOverrides();
      setActionMessage(null);
    }
  }, [isOpen]);

  const loadCurrentOverrides = async () => {
    try {
      setLoading(true);
      const [data, config] = await Promise.all([fetchThresholdOverrides(), fetchStrategyConfig()]);
      setPremiumCap(config.option_selection.max_option_premium);
      setAdxThreshold(config.tunables.adx_threshold);
      setRvolThreshold(config.tunables.rvol_threshold);
      setMinConfirmationScore(config.tunables.min_confirmation_score);
      setStratBMinConfirmation(config.tunables.strat_b_min_confirmation);
      setBoxMaxHeightAtr(config.tunables.box_max_height_atr);
      setBbWidthPercentile(config.tunables.bb_width_percentile_threshold);
      setBullDerivScore(2);
      setBearDerivScore(2);
      if (data) {
        if (data.max_option_premium_cap !== null && data.max_option_premium_cap !== undefined) {
          setPremiumCap(data.max_option_premium_cap);
        }
        if (data.adx_threshold !== null && data.adx_threshold !== undefined) {
          setAdxThreshold(data.adx_threshold);
        }
        if (data.rvol_threshold !== null && data.rvol_threshold !== undefined) {
          setRvolThreshold(data.rvol_threshold);
        }
        if (data.min_confirmation_score !== null && data.min_confirmation_score !== undefined) {
          setMinConfirmationScore(data.min_confirmation_score);
        }
        if (data.strat_b_min_confirmation !== null && data.strat_b_min_confirmation !== undefined) {
          setStratBMinConfirmation(data.strat_b_min_confirmation);
        }
        if (data.box_max_height_atr !== null && data.box_max_height_atr !== undefined) {
          setBoxMaxHeightAtr(data.box_max_height_atr);
        }
        if (data.bull_derivatives_score !== null && data.bull_derivatives_score !== undefined) {
          setBullDerivScore(data.bull_derivatives_score);
        }
        if (data.bear_derivatives_score !== null && data.bear_derivatives_score !== undefined) {
          setBearDerivScore(data.bear_derivatives_score);
        }
        if (data.bb_width_percentile !== null && data.bb_width_percentile !== undefined) {
          setBbWidthPercentile(data.bb_width_percentile);
        }
        setBypassWindow(Boolean(data.bypass_entry_window));
      }
    } catch (err: any) {
      console.error("Failed to load threshold overrides", err);
    } finally {
      setLoading(false);
    }
  };

  const handleSaveOverrides = async () => {
    try {
      setLoading(true);
      setActionMessage(null);
      await updateThresholdOverrides({
        max_option_premium_cap: Number(premiumCap),
        rvol_threshold: Number(rvolThreshold),
        min_confirmation_score: Number(minConfirmationScore),
        strat_b_min_confirmation: Number(stratBMinConfirmation),
        box_max_height_atr: Number(boxMaxHeightAtr),
        bull_derivatives_score: Number(bullDerivScore),
        bear_derivatives_score: Number(bearDerivScore),
        bb_width_percentile: Number(bbWidthPercentile),
        bypass_entry_window: bypassWindow,
      });
      setActionMessage({ type: "success", text: "Threshold overrides saved and active immediately!" });
      onSuccess();
    } catch (err: any) {
      setActionMessage({ type: "error", text: `Failed to save: ${err.message}` });
    } finally {
      setLoading(false);
    }
  };

  const handleResetDefaults = async () => {
    try {
      setLoading(true);
      setActionMessage(null);
      await resetThresholdOverrides();
      await loadCurrentOverrides();
      setActionMessage({ type: "success", text: "Overrides cleared; saved strategy settings restored." });
      onSuccess();
    } catch (err: any) {
      setActionMessage({ type: "error", text: `Reset failed: ${err.message}` });
    } finally {
      setLoading(false);
    }
  };

  const handleForceTrigger = async () => {
    const isCall = forceDirection === "BULLISH";
    const confirmed = confirm(
      `Force trigger immediate ${forceDirection} (${isCall ? "CALL / CE" : "PUT / PE"}) entry using ${
        "Volatility Breakout"
      }?\n\nAutomated contract selection under premium cap (₹${premiumCap}), position sizing, -25% hard stop, and the configured Strategy B protections will be applied.`
    );
    if (!confirmed) return;

    try {
      setForceLoading(true);
      setActionMessage(null);
      const res = await forceStrategyEntry({
        strategy: forceStrategy,
        direction: forceDirection,
        option_type: isCall ? "CALL" : "PUT",
        override_premium_cap: Number(premiumCap),
      });

      if (res.status === "TRADE_OPENED") {
        setActionMessage({
          type: "success",
          text: `Trade Entered! Symbol: ${res.trade?.contract_symbol || "NIFTY Option"} @ ₹${res.trade?.entry_option_price} (${res.trade?.quantity} qty)`,
        });
        onSuccess();
      } else if (res.status === "CONTRACT_SELECTION_FAILED") {
        setActionMessage({
          type: "error",
          text: `Contract Selection Failed: ${res.reason}. Try raising the Max Option Premium Cap above ₹${premiumCap}.`,
        });
      } else {
        setActionMessage({ type: "error", text: `Engine response: ${res.status} (${res.message || res.reason || ""})` });
      }
    } catch (err: any) {
      setActionMessage({ type: "error", text: `Force trigger failed: ${err.message}` });
    } finally {
      setForceLoading(false);
    }
  };

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/70 backdrop-blur-sm animate-in fade-in duration-200">
      <div className="bg-slate-900 border border-slate-700 w-full max-w-2xl rounded-xl shadow-2xl overflow-hidden flex flex-col max-h-[90vh]">
        {/* Header */}
        <div className="bg-slate-950 px-6 py-4 border-b border-slate-800 flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 rounded-lg bg-amber-500/20 text-amber-400 flex items-center justify-center border border-amber-500/30">
              <Sliders className="w-4 h-4" />
            </div>
            <div>
              <h3 className="text-base font-bold text-slate-100 flex items-center gap-2">
                Manual Threshold Overrides & Force Trigger
              </h3>
              <p className="text-xs text-slate-400">
                Tune live trigger sensitivity or force instant setup entry with automated risk safeguards.
              </p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="p-1 rounded-lg text-slate-400 hover:text-slate-200 hover:bg-slate-800 transition"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Content Body */}
        <div className="p-6 overflow-y-auto space-y-6">
          {actionMessage && (
            <div
              className={`p-3 rounded-lg text-xs flex items-center gap-2 border ${
                actionMessage.type === "success"
                  ? "bg-emerald-500/10 border-emerald-500/30 text-emerald-300"
                  : "bg-rose-500/10 border-rose-500/30 text-rose-300"
              }`}
            >
              {actionMessage.type === "success" ? (
                <Check className="w-4 h-4 flex-shrink-0" />
              ) : (
                <AlertTriangle className="w-4 h-4 flex-shrink-0" />
              )}
              <span>{actionMessage.text}</span>
            </div>
          )}

          {/* Section 1: Dynamic Threshold Tuning */}
          <div>
            <h4 className="text-xs font-bold text-slate-300 uppercase tracking-wider mb-3 flex items-center gap-1.5">
              <Sliders className="w-3.5 h-3.5 text-blue-400" />
              Live Strategy Trigger Thresholds
            </h4>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              {/* Max Premium Cap */}
              <div className="bg-slate-950/80 p-3.5 rounded-lg border border-slate-800">
                <div className="flex justify-between items-center mb-1.5">
                  <label className="text-xs font-semibold text-slate-200 flex items-center gap-1">
                    Strategy B Max Option Premium Cap
                  </label>
                  <span className="text-xs font-bold text-cyan-400">₹{premiumCap}</span>
                </div>
                <input
                  type="range"
                  min="20"
                  max="250"
                  step="5"
                  value={premiumCap}
                  onChange={(e) => setPremiumCap(Number(e.target.value))}
                  className="w-full h-1.5 bg-slate-800 rounded-lg appearance-none cursor-pointer accent-cyan-400"
                />
                <p className="text-[10px] text-slate-400 mt-1">
                  Strategy B / manual force-entry only. Strategy A V3 uses delta-based contract selection.
                </p>
              </div>

              {/* ADX Threshold */}
              <div className="bg-slate-950/80 p-3.5 rounded-lg border border-slate-800">
                <div className="flex justify-between items-center mb-1.5">
                  <label className="text-xs font-semibold text-slate-200">Legacy ADX Compatibility</label>
                  <span className="text-xs font-bold text-indigo-400">{adxThreshold} pts</span>
                </div>
                <input
                  type="range"
                  min="10"
                  max="35"
                  step="1"
                  value={adxThreshold}
                  disabled
                  className="w-full h-1.5 bg-slate-800 rounded-lg appearance-none cursor-not-allowed opacity-50 accent-indigo-400"
                />
                <p className="text-[10px] text-slate-400 mt-1">
                  Display-only compatibility value. Strategy A V3 does not use a hard ADX floor; its momentum-health thresholds are configured in Parameters.
                </p>
              </div>

              {/* RVOL Threshold */}
              <div className="bg-slate-950/80 p-3.5 rounded-lg border border-slate-800">
                <div className="flex justify-between items-center mb-1.5">
                  <label className="text-xs font-semibold text-slate-200">RVOL Threshold (Strategy B)</label>
                  <span className="text-xs font-bold text-amber-400">{rvolThreshold}x</span>
                </div>
                <input
                  type="range"
                  min="0.80"
                  max="2.50"
                  step="0.05"
                  value={rvolThreshold}
                  onChange={(e) => setRvolThreshold(Number(e.target.value))}
                  className="w-full h-1.5 bg-slate-800 rounded-lg appearance-none cursor-pointer accent-amber-400"
                />
                <p className="text-[10px] text-slate-400 mt-1">
                  Default 1.20x. Adds a confirmation point when futures volume meets this threshold.
                </p>
              </div>

              {/* Min Confirmation Score */}
              <div className="bg-slate-950/80 p-3.5 rounded-lg border border-slate-800">
                <div className="flex justify-between items-center mb-1.5">
                  <label className="text-xs font-semibold text-slate-200">Required Confirmation Points</label>
                  <span className="text-xs font-bold text-cyan-400">{minConfirmationScore} / 6 pts</span>
                </div>
                <input
                  type="range"
                  min="1"
                  max="6"
                  step="1"
                  value={minConfirmationScore}
                  onChange={(e) => setMinConfirmationScore(Number(e.target.value))}
                  className="w-full h-1.5 bg-slate-800 rounded-lg appearance-none cursor-pointer accent-cyan-400"
                />
                <p className="text-[10px] text-slate-400 mt-1">
                  Default 2 of 6 points: Supertrend, VWAP, option flow, futures OI buildup, RVOL, and pullback volume.
                </p>
              </div>

              {/* Derivatives Confirmation Score */}
              <div className="bg-slate-950/80 p-3.5 rounded-lg border border-slate-800">
                <div className="flex justify-between items-center mb-1.5">
                  <label className="text-xs font-semibold text-slate-200">Derivatives Flow Threshold</label>
                  <span className="text-xs font-bold text-emerald-400">+{bullDerivScore} / 5.0</span>
                </div>
                <input
                  type="range"
                  min="0.5"
                  max="3.5"
                  step="0.5"
                  value={bullDerivScore}
                  onChange={(e) => {
                    const v = Number(e.target.value);
                    setBullDerivScore(v);
                    setBearDerivScore(v);
                  }}
                  className="w-full h-1.5 bg-slate-800 rounded-lg appearance-none cursor-pointer accent-emerald-400"
                />
                <p className="text-[10px] text-slate-400 mt-1">
                  Default +2.0. Lower to +1.0 if PCR / futures buildup confirmation is moderate.
                </p>
              </div>

              {/* BB Width Percentile (Strategy B) */}
              <div className="bg-slate-950/80 p-3.5 rounded-lg border border-slate-800">
                <div className="flex justify-between items-center mb-1.5">
                  <label className="text-xs font-semibold text-slate-200">BB Width Percentile (Strat B)</label>
                  <span className="text-xs font-bold text-purple-400">{bbWidthPercentile}th %ile</span>
                </div>
                <input
                  type="range"
                  min="20"
                  max="35"
                  step="5"
                  value={bbWidthPercentile}
                  onChange={(e) => setBbWidthPercentile(Number(e.target.value))}
                  className="w-full h-1.5 bg-slate-800 rounded-lg appearance-none cursor-pointer accent-purple-400"
                />
                <p className="text-[10px] text-slate-400 mt-1">
                  Default 25.0%. Squeeze detected when BB width is below this historical percentile.
                </p>
              </div>

              {/* Strategy B Required Confirmation Points */}
              <div className="bg-slate-950/80 p-3.5 rounded-lg border border-slate-800">
                <div className="flex justify-between items-center mb-1.5">
                  <label className="text-xs font-semibold text-slate-200">Strat B Min Confirmation</label>
                  <span className="text-xs font-bold text-amber-400">{stratBMinConfirmation} / 6 pts</span>
                </div>
                <input
                  type="range"
                  min="1"
                  max="6"
                  step="1"
                  value={stratBMinConfirmation}
                  onChange={(e) => setStratBMinConfirmation(Number(e.target.value))}
                  className="w-full h-1.5 bg-slate-800 rounded-lg appearance-none cursor-pointer accent-amber-400"
                />
                <p className="text-[10px] text-slate-400 mt-1">
                  Default 3 pts. Scores RVOL, body, close location, VWAP, derivatives and futures OI; a nearby OI wall subtracts one point.
                </p>
              </div>

              {/* Strategy B Max Box Height (ATR) */}
              <div className="bg-slate-950/80 p-3.5 rounded-lg border border-slate-800">
                <div className="flex justify-between items-center mb-1.5">
                  <label className="text-xs font-semibold text-slate-200">Strat B Max Box Height</label>
                  <span className="text-xs font-bold text-rose-400">{boxMaxHeightAtr} ATR</span>
                </div>
                <input
                  type="range"
                  min="1.10"
                  max="1.60"
                  step="0.10"
                  value={boxMaxHeightAtr}
                  onChange={(e) => setBoxMaxHeightAtr(Number(e.target.value))}
                  className="w-full h-1.5 bg-slate-800 rounded-lg appearance-none cursor-pointer accent-rose-400"
                />
                <p className="text-[10px] text-slate-400 mt-1">
                  Default 1.30 ATR. Rejects wide non-compact candle ranges as invalid consolidation boxes.
                </p>
              </div>
            </div>

            {/* Bypass Entry Window (Strategy B / simulation only) Toggle */}
            <div className="mt-3 bg-slate-950/80 p-3.5 rounded-lg border border-slate-800 flex items-center justify-between">
              <div>
                <span className="text-xs font-semibold text-slate-200 block">
                  Bypass Session Window (Strategy B only)
                </span>
                <span className="text-[10px] text-slate-400">
                  Allow live testing / trigger evaluation outside regular intraday trading hours.
                </span>
              </div>
              <button
                type="button"
                onClick={() => setBypassWindow(!bypassWindow)}
                className={`w-12 h-6 rounded-full transition-colors relative ${
                  bypassWindow ? "bg-amber-500" : "bg-slate-700"
                }`}
              >
                <div
                  className={`w-4 h-4 rounded-full bg-white transition-transform transform ${
                    bypassWindow ? "translate-x-7" : "translate-x-1"
                  } top-1 absolute`}
                />
              </button>
            </div>

            {/* Threshold Action Buttons */}
            <div className="mt-3 flex gap-2 justify-end">
              <button
                type="button"
                onClick={handleResetDefaults}
                disabled={loading}
                className="px-3 py-1.5 text-xs font-medium text-slate-300 bg-slate-800 hover:bg-slate-700 rounded-lg transition flex items-center gap-1.5 border border-slate-700"
              >
                <RotateCcw className="w-3.5 h-3.5" />
                Reset Defaults
              </button>
              <button
                type="button"
                onClick={handleSaveOverrides}
                disabled={loading}
                className="px-4 py-1.5 text-xs font-bold text-white bg-blue-600 hover:bg-blue-500 rounded-lg transition flex items-center gap-1.5 shadow"
              >
                {loading && <RefreshCw className="w-3.5 h-3.5 animate-spin" />}
                Apply Overrides
              </button>
            </div>
          </div>

          <div className="border-t border-slate-800 pt-4">
            {/* Section 2: 1-Click Force Entry */}
            <div className="bg-amber-500/5 border border-amber-500/20 rounded-xl p-4">
              <div className="flex items-center gap-2 mb-2">
                <Flame className="w-4 h-4 text-amber-400" />
                <h4 className="text-xs font-bold text-amber-300 uppercase tracking-wider">
                  Strategy B Manual Force Entry
                </h4>
              </div>
              <p className="text-xs text-slate-400 mb-4">
                Execute an immediate simulated/live order without waiting for candle close or technical breakout.
                Contracts will be selected under the active ₹{premiumCap} cap, and all risk protections
                (position sizing, -25% option hard SL, +1R trailing ladder) remain 100% active.
              </p>

              <div className="grid grid-cols-2 gap-3 mb-4">
                <div>
                  <label className="text-[11px] font-semibold text-slate-300 block mb-1">Strategy Rulebook</label>
                  <select
                    value={forceStrategy}
                    disabled
                    className="w-full bg-slate-900 border border-slate-700 text-xs text-slate-200 rounded-lg p-2 focus:ring-1 focus:ring-amber-400 outline-none"
                  >
                    <option value="TREND_PULLBACK">Strategy A: Trend Pullback</option>
                    <option value="VOLATILITY_BREAKOUT">Strategy B: Volatility Breakout</option>
                  </select>
                </div>

                <div>
                  <label className="text-[11px] font-semibold text-slate-300 block mb-1">Trade Direction</label>
                  <div className="grid grid-cols-2 gap-2">
                    <button
                      type="button"
                      onClick={() => setForceDirection("BULLISH")}
                      className={`py-2 rounded-lg text-xs font-bold transition border ${
                        forceDirection === "BULLISH"
                          ? "bg-emerald-500/20 border-emerald-500 text-emerald-300 shadow"
                          : "bg-slate-900 border-slate-800 text-slate-400 hover:border-slate-700"
                      }`}
                    >
                      BULLISH (CALL)
                    </button>
                    <button
                      type="button"
                      onClick={() => setForceDirection("BEARISH")}
                      className={`py-2 rounded-lg text-xs font-bold transition border ${
                        forceDirection === "BEARISH"
                          ? "bg-rose-500/20 border-rose-500 text-rose-300 shadow"
                          : "bg-slate-900 border-slate-800 text-slate-400 hover:border-slate-700"
                      }`}
                    >
                      BEARISH (PUT)
                    </button>
                  </div>
                </div>
              </div>

              <button
                type="button"
                onClick={handleForceTrigger}
                disabled={forceLoading}
                className={`w-full py-2.5 rounded-lg text-xs font-bold text-white transition flex items-center justify-center gap-2 shadow-lg ${
                  forceDirection === "BULLISH"
                    ? "bg-emerald-600 hover:bg-emerald-500 shadow-emerald-900/40"
                    : "bg-rose-600 hover:bg-rose-500 shadow-rose-900/40"
                }`}
              >
                {forceLoading ? (
                  <RefreshCw className="w-4 h-4 animate-spin" />
                ) : (
                  <Zap className="w-4 h-4" />
                )}
                ⚡ FORCE TRIGGER {forceDirection} ({forceDirection === "BULLISH" ? "BUY CALL" : "BUY PUT"}) ENTRY
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};
