"use client";

import React, { useState } from "react";
import { Check, Clock, DollarSign, Layers, RotateCcw, Save, ShieldAlert, Sliders } from "lucide-react";
import { StrategyStatusData, updateStrategyConfig } from "../../lib/api";

interface TabParametersProps {
  status: StrategyStatusData | null;
  onRefresh: () => void;
}

export const TabParameters: React.FC<TabParametersProps> = ({ status, onRefresh }) => {
  if (!status) return null;

  const [form, setForm] = useState(status.config);
  const [isSaving, setIsSaving] = useState(false);
  const [saveSuccess, setSaveSuccess] = useState(false);

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      setIsSaving(true);
      setSaveSuccess(false);
      const response = await updateStrategyConfig(form);
      if (response?.config) setForm(response.config);
      setSaveSuccess(true);
      setTimeout(() => setSaveSuccess(false), 3000);
      await onRefresh();
    } catch (e: any) {
      alert(`Save failed: ${e.message}`);
    } finally {
      setIsSaving(false);
    }
  };

  const handleReset = () => {
    setForm(status.config);
  };

  return (
    <form onSubmit={handleSave} className="space-y-6">
      <fieldset className="bg-slate-900 border border-slate-800 rounded-xl p-5 space-y-3">
        <legend className="text-sm font-semibold text-cyan-300">Strategy B — Compression Breakout</legend>
        <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
          {([
            ["strat_b_min_confirmation", "Required confirmations / 6", 1, 6, 1],
            ["bb_width_percentile_threshold", "BB width percentile ceiling", 20, 35, 1],
            ["box_max_height_atr", "Maximum box height (ATR)", 1.1, 1.6, 0.05],
            ["compression_lookback_bars", "Compression window (bars)", 6, 10, 1],
            ["box_max_age_bars", "Maximum box age (bars)", 1, 20, 1],
            ["breakout_buffer_atr", "Breakout buffer (ATR)", 0.05, 0.1, 0.01],
            ["breakout_max_extension_atr", "Anti-chase limit (ATR)", 0.6, 1, 0.05],
          ] as const).map(([key, label, min, max, step]) => (
            <label key={key} className="text-xs text-slate-400">{label}
              <input type="number" min={min} max={max} step={step} value={form.tunables[key]}
                onChange={e => setForm({...form, tunables: {...form.tunables, [key]: Number(e.target.value)}})}
                className="block w-full mt-1 bg-slate-950 border border-slate-700 rounded p-2 text-slate-100" />
            </label>
          ))}
          <label className="text-xs text-slate-400">Strategy B entry start (IST)
            <input type="time" value={form.session.strategy_b_no_new_trade_before}
              onChange={e => setForm({...form, session: {...form.session, strategy_b_no_new_trade_before: e.target.value}})}
              className="block w-full mt-1 bg-slate-950 border border-slate-700 rounded p-2" />
          </label>
          {([ ["max_lots_per_trade", "Max lots / trade", 1, 1],
               ["entry_order_timeout_sec", "Entry cancel timeout (seconds)", 1, 1],
               ["breakeven_buffer_points", "Breakeven buffer (points)", 0, 0.5],
               ["max_failed_trades_per_strategy", "Max failed trades / strategy / day", 1, 1],
               ["max_trades_per_strategy_per_day", "Max trades / strategy / day", 1, 1] ] as const).map(([key,label,min,step]) => (
            <label key={key} className="text-xs text-slate-400">{label}
              <input type="number" min={min} step={step} value={form.risk[key]}
                onChange={e => setForm({...form, risk: {...form.risk, [key]: Number(e.target.value)}})}
                className="block w-full mt-1 bg-slate-950 border border-slate-700 rounded p-2 text-slate-100" />
            </label>
          ))}
        </div>
        <p className="text-xs text-slate-400">Changing settings resets the locked box. Body, RVOL, VWAP and OI are scored confirmations, not individual entry gates.</p>
      </fieldset>
      <div className="flex items-center justify-between pb-2 border-b border-slate-800">
        <div>
          <h3 className="text-base font-bold text-slate-100 flex items-center gap-2">
            <Sliders className="w-4 h-4 text-cyan-400" />
            Strategy Parameters & Risk Configuration
          </h3>
          <p className="text-xs text-slate-400">
            Tune maximum option premium caps, sizing guardrails, intraday session timers, and indicator thresholds.
          </p>
        </div>

        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={handleReset}
            className="px-3 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded text-xs font-semibold flex items-center gap-1.5 transition-colors"
          >
            <RotateCcw className="w-3.5 h-3.5" />
            Reset
          </button>
          <button
            type="submit"
            disabled={isSaving}
            className="px-4 py-1.5 bg-cyan-600 hover:bg-cyan-500 text-white rounded text-xs font-semibold flex items-center gap-1.5 transition-colors disabled:opacity-50"
          >
            {saveSuccess ? (
              <>
                <Check className="w-3.5 h-3.5 text-emerald-300" />
                Saved!
              </>
            ) : (
              <>
                <Save className="w-3.5 h-3.5" />
                {isSaving ? "Saving..." : "Save Parameters"}
              </>
            )}
          </button>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        {/* 1. Option Selection & Maximum Premium Cap */}
        <div className="bg-slate-900/90 border border-slate-800 rounded-xl p-5 shadow-lg space-y-4">
          <div className="flex items-center gap-2 pb-2 border-b border-slate-800 text-cyan-400 text-sm font-bold uppercase tracking-wider">
            <Layers className="w-4 h-4" />
            Option Selection & Max Premium Cap
          </div>

          <div>
            <div className="flex justify-between items-center mb-1">
              <label className="text-xs font-semibold text-slate-200">
                Max Option Premium Cap (₹)
              </label>
              <span className="text-sm font-extrabold text-cyan-400">
                ₹{form.option_selection.max_option_premium.toFixed(2)}
              </span>
            </div>
            <input
              type="range"
              min="20"
              max="200"
              step="5"
              value={form.option_selection.max_option_premium}
              onChange={(e) =>
                setForm({
                  ...form,
                  option_selection: {
                    ...form.option_selection,
                    max_option_premium: parseFloat(e.target.value),
                  },
                })
              }
              className="w-full accent-cyan-500 cursor-pointer"
            />
            <p className="text-[11px] text-slate-400 mt-1">
              Contracts exceeding this premium are excluded. If ATM is ₹120 and +1 OTM is ₹62, the system selects the ₹62 contract.
            </p>
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="text-xs text-slate-400 block mb-1">Min Premium Floor (₹)</label>
              <input
                type="number"
                min="5"
                max="50"
                step="1"
                value={form.option_selection.min_option_premium}
                onChange={(e) =>
                  setForm({
                    ...form,
                    option_selection: {
                      ...form.option_selection,
                      min_option_premium: parseFloat(e.target.value),
                    },
                  })
                }
                className="w-full bg-slate-950 border border-slate-800 rounded px-3 py-1.5 text-xs text-slate-100"
              />
            </div>

            <div>
              <label className="text-xs text-slate-400 block mb-1">Max OTM Strikes</label>
              <input
                type="number"
                min="1"
                max="8"
                value={form.option_selection.max_otm_strikes}
                onChange={(e) =>
                  setForm({
                    ...form,
                    option_selection: {
                      ...form.option_selection,
                      max_otm_strikes: parseInt(e.target.value, 10),
                    },
                  })
                }
                className="w-full bg-slate-950 border border-slate-800 rounded px-3 py-1.5 text-xs text-slate-100"
              />
            </div>
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="text-xs text-slate-400 block mb-1">Min Open Interest</label>
              <input
                type="number"
                step="5000"
                value={form.option_selection.min_open_interest}
                onChange={(e) =>
                  setForm({
                    ...form,
                    option_selection: {
                      ...form.option_selection,
                      min_open_interest: parseInt(e.target.value, 10),
                    },
                  })
                }
                className="w-full bg-slate-950 border border-slate-800 rounded px-3 py-1.5 text-xs text-slate-100"
              />
            </div>

            <div>
              <label className="text-xs text-slate-400 block mb-1">Max Spread %</label>
              <input
                type="number"
                step="0.5"
                value={form.option_selection.max_bid_ask_spread_pct}
                onChange={(e) =>
                  setForm({
                    ...form,
                    option_selection: {
                      ...form.option_selection,
                      max_bid_ask_spread_pct: parseFloat(e.target.value),
                    },
                  })
                }
                className="w-full bg-slate-950 border border-slate-800 rounded px-3 py-1.5 text-xs text-slate-100"
              />
            </div>
          </div>

          <div className="pt-2">
            <label className="flex items-center gap-2 cursor-pointer text-xs text-slate-300">
              <input
                type="checkbox"
                checked={form.option_selection.prefer_premium_closest_to_cap}
                onChange={(e) =>
                  setForm({
                    ...form,
                    option_selection: {
                      ...form.option_selection,
                      prefer_premium_closest_to_cap: e.target.checked,
                    },
                  })
                }
                className="rounded accent-cyan-500"
              />
              Prefer eligible contract closest to cap (e.g. ₹62 instead of ₹25)
            </label>
          </div>
        </div>

        {/* 2. Capital & Risk Guardrails */}
        <div className="bg-slate-900/90 border border-slate-800 rounded-xl p-5 shadow-lg space-y-4">
          <div className="flex items-center gap-2 pb-2 border-b border-slate-800 text-rose-400 text-sm font-bold uppercase tracking-wider">
            <DollarSign className="w-4 h-4" />
            Capital Allocation & Risk Ceilings
          </div>

          <div>
            <div className="flex justify-between items-center mb-1">
              <label className="text-xs font-semibold text-slate-200">
                Max Trade Capital (₹)
              </label>
              <span className="text-sm font-extrabold text-emerald-400">
                ₹{form.risk.max_trade_capital.toLocaleString()}
              </span>
            </div>
            <input
              type="number"
              step="5000"
              value={form.risk.max_trade_capital}
              onChange={(e) =>
                setForm({
                  ...form,
                  risk: {
                    ...form.risk,
                    max_trade_capital: parseFloat(e.target.value),
                  },
                })
              }
              className="w-full bg-slate-950 border border-slate-800 rounded px-3 py-1.5 text-xs text-slate-100"
            />
            <p className="text-[11px] text-slate-400 mt-1">
              Hard capital cap per single trade. Total entry cost will never exceed this budget.
            </p>
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="text-xs text-slate-400 block mb-1">Account Risk % / Trade</label>
              <input
                type="number"
                step="0.1"
                value={form.risk.risk_per_trade_pct_of_account}
                onChange={(e) =>
                  setForm({
                    ...form,
                    risk: {
                      ...form.risk,
                      risk_per_trade_pct_of_account: parseFloat(e.target.value),
                    },
                  })
                }
                className="w-full bg-slate-950 border border-slate-800 rounded px-3 py-1.5 text-xs text-slate-100"
              />
            </div>

            <div>
              <label className="text-xs text-slate-400 block mb-1">Option Hard Stop %</label>
              <input
                type="number"
                step="5"
                value={form.risk.option_hard_stop_pct}
                onChange={(e) =>
                  setForm({
                    ...form,
                    risk: {
                      ...form.risk,
                      option_hard_stop_pct: parseFloat(e.target.value),
                    },
                  })
                }
                className="w-full bg-slate-950 border border-slate-800 rounded px-3 py-1.5 text-xs text-slate-100"
              />
            </div>
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="text-xs text-slate-400 block mb-1">Max Trades Per Day</label>
              <input
                type="number"
                min="1"
                max="20"
                step="1"
                required
                value={form.risk.max_trades_per_day}
                onChange={(e) =>
                  setForm({
                    ...form,
                    risk: {
                      ...form.risk,
                      max_trades_per_day: Number(e.target.value) || 1,
                    },
                  })
                }
                className="w-full bg-slate-950 border border-slate-800 rounded px-3 py-1.5 text-xs text-slate-100"
              />
            </div>

            <div>
              <label className="text-xs text-slate-400 block mb-1">Cooldown After Loss (min)</label>
              <input
                type="number"
                value={form.risk.cooldown_after_loss_min}
                onChange={(e) =>
                  setForm({
                    ...form,
                    risk: {
                      ...form.risk,
                      cooldown_after_loss_min: parseInt(e.target.value, 10),
                    },
                  })
                }
                className="w-full bg-slate-950 border border-slate-800 rounded px-3 py-1.5 text-xs text-slate-100"
              />
            </div>
          </div>
        </div>

        {/* 3. Session Timers (IST) */}
        <div className="bg-slate-900/90 border border-slate-800 rounded-xl p-5 shadow-lg space-y-4">
          <div className="flex items-center gap-2 pb-2 border-b border-slate-800 text-blue-400 text-sm font-bold uppercase tracking-wider">
            <Clock className="w-4 h-4" />
            Intraday Session Timers (IST)
          </div>

          <div className="grid grid-cols-3 gap-3">
            <div>
              <label className="text-xs text-slate-400 block mb-1">No New Trades Before</label>
              <input
                type="text"
                value={form.session.no_new_trade_before}
                onChange={(e) =>
                  setForm({
                    ...form,
                    session: {
                      ...form.session,
                      no_new_trade_before: e.target.value,
                    },
                  })
                }
                className="w-full bg-slate-950 border border-slate-800 rounded px-3 py-1.5 text-xs text-slate-100 text-center font-mono"
              />
            </div>

            <div>
              <label className="text-xs text-slate-400 block mb-1">No New Trades After</label>
              <input
                type="text"
                value={form.session.no_new_trade_after}
                onChange={(e) =>
                  setForm({
                    ...form,
                    session: {
                      ...form.session,
                      no_new_trade_after: e.target.value,
                    },
                  })
                }
                className="w-full bg-slate-950 border border-slate-800 rounded px-3 py-1.5 text-xs text-slate-100 text-center font-mono"
              />
            </div>

            <div>
              <label className="text-xs text-slate-400 block mb-1">Force Square-Off Time</label>
              <input
                type="text"
                value={form.session.force_exit_time}
                onChange={(e) =>
                  setForm({
                    ...form,
                    session: {
                      ...form.session,
                      force_exit_time: e.target.value,
                    },
                  })
                }
                className="w-full bg-slate-950 border border-slate-800 rounded px-3 py-1.5 text-xs text-rose-400 font-bold text-center font-mono"
              />
            </div>
          </div>
          <p className="text-[11px] text-slate-400">
            Open intraday positions are automatically squared off at 15:20 IST to avoid broker auction risk.
          </p>
        </div>

        {/* 4. Strategy A — Trend Pullback Parameters */}
        <div className="bg-slate-900/90 border border-cyan-800/40 rounded-xl p-5 shadow-lg space-y-4">
          <div className="flex items-center gap-2 pb-2 border-b border-slate-800 text-cyan-400 text-sm font-bold uppercase tracking-wider">
            <Sliders className="w-4 h-4" />
            Strategy A — Trend Pullback Parameters
          </div>
          <p className="text-[11px] text-slate-500 -mt-1">
            Controls impulse detection sensitivity, confirmation bar requirements, and position sizing equity base for Strategy A.
          </p>

          <div className="grid grid-cols-1 gap-4">
            <label className="block">
              <span className="text-xs text-slate-400 font-medium">Account equity for position sizing (₹)</span>
              <span className="block text-[10px] text-slate-600 mb-1">Used to compute lot size relative to max capital per trade.</span>
              <input type="number" min="1" value={form.risk.account_equity ?? 500000}
                onChange={e => setForm({...form, risk: {...form.risk, account_equity: Number(e.target.value)}})}
                className="block w-full bg-slate-950 border border-slate-800 rounded px-3 py-2 text-xs text-slate-100" />
            </label>

            <label className="block">
              <span className="text-xs text-slate-400 font-medium">Normalized EMA slope threshold</span>
              <span className="block text-[10px] text-slate-600 mb-1">Minimum EMA slope (0–1 normalised) to confirm bullish/bearish impulse. Lower = more signals. Default: 0.10.</span>
              <input type="number" min="0.01" max="1" step="0.01" value={form.tunables.ema_slope_threshold ?? 0.10}
                onChange={e => setForm({...form, tunables: {...form.tunables, ema_slope_threshold: Number(e.target.value)}})}
                className="block w-full bg-slate-950 border border-slate-800 rounded px-3 py-2 text-xs text-slate-100" />
            </label>

            <label className="block">
              <span className="text-xs text-slate-400 font-medium">Required confirmation score (of 6)</span>
              <span className="block text-[10px] text-slate-600 mb-1">Minimum number of confirmation checklist items that must pass before entry. Lower = easier entry. Default: 2.</span>
              <input type="number" min="1" max="6" step="1" value={form.tunables.min_confirmation_score ?? 2}
                onChange={e => setForm({...form, tunables: {...form.tunables, min_confirmation_score: Number(e.target.value)}})}
                className="block w-full bg-slate-950 border border-slate-800 rounded px-3 py-2 text-xs text-slate-100" />
            </label>
          </div>
        </div>

        <div className="bg-slate-900/90 border border-slate-800 rounded-xl p-5 shadow-lg space-y-4">
          <div className="flex items-center gap-2 pb-2 border-b border-slate-800 text-amber-400 text-sm font-bold uppercase tracking-wider">
            <ShieldAlert className="w-4 h-4" />
            Strategy Tunables & Activation
          </div>

          <div className="grid grid-cols-2 gap-4">
            <label className="flex items-center gap-2 cursor-pointer text-xs text-slate-200">
              <input
                type="checkbox"
                checked={form.tunables.trend_pullback_enabled}
                onChange={(e) =>
                  setForm({
                    ...form,
                    tunables: {
                      ...form.tunables,
                      trend_pullback_enabled: e.target.checked,
                    },
                  })
                }
                className="rounded accent-cyan-500"
              />
              Enable Strategy A (Trend Pullback)
            </label>

            <label className="flex items-center gap-2 cursor-pointer text-xs text-slate-200">
              <input
                type="checkbox"
                checked={form.tunables.volatility_breakout_enabled}
                onChange={(e) =>
                  setForm({
                    ...form,
                    tunables: {
                      ...form.tunables,
                      volatility_breakout_enabled: e.target.checked,
                    },
                  })
                }
                className="rounded accent-cyan-500"
              />
              Enable Strategy B (Volatility Breakout)
            </label>
          </div>

          <div className="grid grid-cols-3 gap-3">
            <div>
              <label className="text-xs text-slate-400 block mb-1">ADX Threshold</label>
              <input
                type="number"
                value={form.tunables.adx_threshold}
                onChange={(e) =>
                  setForm({
                    ...form,
                    tunables: {
                      ...form.tunables,
                      adx_threshold: parseFloat(e.target.value),
                    },
                  })
                }
                className="w-full bg-slate-950 border border-slate-800 rounded px-3 py-1.5 text-xs text-slate-100"
              />
            </div>

            <div>
              <label className="text-xs text-slate-400 block mb-1">RVOL Threshold</label>
              <input
                type="number"
                step="0.05"
                value={form.tunables.rvol_threshold}
                onChange={(e) =>
                  setForm({
                    ...form,
                    tunables: {
                      ...form.tunables,
                      rvol_threshold: parseFloat(e.target.value),
                    },
                  })
                }
                className="w-full bg-slate-950 border border-slate-800 rounded px-3 py-1.5 text-xs text-slate-100"
              />
            </div>

            <div>
              <label className="text-xs text-slate-400 block mb-1">Evaluation (sec)</label>
              <input
                type="number"
                min="1"
                max="10"
                value={form.tunables.evaluation_interval_sec}
                onChange={(e) =>
                  setForm({
                    ...form,
                    tunables: {
                      ...form.tunables,
                      evaluation_interval_sec: parseInt(e.target.value, 10),
                    },
                  })
                }
                className="w-full bg-slate-950 border border-slate-800 rounded px-3 py-1.5 text-xs text-slate-100"
              />
            </div>
          </div>
        </div>
      </div>
    </form>
  );
};
