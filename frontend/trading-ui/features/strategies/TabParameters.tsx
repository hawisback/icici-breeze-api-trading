"use client";

import React, { useState } from "react";
import { Check, Clock, DollarSign, Layers, RotateCcw, Save, ShieldAlert, Sliders, TrendingUp } from "lucide-react";
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
  const strategyC = status.strategy_c_paper;
  const strategyD = status.strategy_d_paper;

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
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div className="bg-slate-900/90 border border-violet-900/60 rounded-xl p-5 shadow-lg space-y-3">
          <div className="flex items-center justify-between gap-3">
            <div>
              <div className="text-xs font-bold uppercase tracking-wider text-violet-300">Strategy C · Validated Runtime</div>
              <div className="text-sm font-bold text-slate-100">DI Continuation V1</div>
            </div>
            <span className="px-2 py-1 rounded text-[10px] font-bold bg-blue-500/10 text-blue-300 border border-blue-500/30">{status.strategies.di_continuation.execution_mode || form.mode}</span>
          </div>
          <div className="grid grid-cols-2 gap-2 text-[10px]">
            <div className="bg-slate-950 rounded p-2"><div className="text-slate-500">Monitor</div><div className="font-bold text-slate-200">{strategyC?.status || "NOT_INITIALIZED"}</div></div>
            <div className="bg-slate-950 rounded p-2"><div className="text-slate-500">Live routing</div><div className={`font-bold ${status.strategies.di_continuation.live_trading_allowed ? "text-rose-300" : "text-slate-400"}`}>{status.strategies.di_continuation.live_trading_allowed ? "ARMED / ALLOWED" : "DISARMED / SAFE"}</div></div>
            <div className="bg-slate-950 rounded p-2 col-span-2"><div className="text-slate-500">Candidate</div><div className="font-mono text-violet-300 truncate">{strategyC?.candidate_id || "--"}</div></div>
          </div>
          <div className="text-[10px] text-slate-500">15m context → native 5m setup → native 1m trigger. The frozen lifecycle is now executed through the shared ActiveTrade/risk/OMS pipeline.</div>
          {strategyC?.candidate_spec_fingerprint && <div className="text-[9px] text-slate-600 font-mono break-all">spec {strategyC.candidate_spec_fingerprint}</div>}
        </div>
        <div className="bg-slate-900/90 border border-fuchsia-900/60 rounded-xl p-5 shadow-lg space-y-3">
          <div className="flex items-center justify-between gap-3">
            <div>
              <div className="text-xs font-bold uppercase tracking-wider text-fuchsia-300">Strategy D · Validated Runtime</div>
              <div className="text-sm font-bold text-slate-100">S&amp;R Momentum Breakout V2</div>
            </div>
            <span className="px-2 py-1 rounded text-[10px] font-bold bg-blue-500/10 text-blue-300 border border-blue-500/30">{status.strategies.sr_momentum_breakout.execution_mode || form.mode}</span>
          </div>
          <div className="grid grid-cols-2 gap-2 text-[10px]">
            <div className="bg-slate-950 rounded p-2"><div className="text-slate-500">Monitor</div><div className="font-bold text-slate-200">{strategyD?.status || "NOT_INITIALIZED"}</div></div>
            <div className="bg-slate-950 rounded p-2"><div className="text-slate-500">Live routing</div><div className={`font-bold ${status.strategies.sr_momentum_breakout.live_trading_allowed ? "text-rose-300" : "text-slate-400"}`}>{status.strategies.sr_momentum_breakout.live_trading_allowed ? "ARMED / ALLOWED" : "DISARMED / SAFE"}</div></div>
            <div className="bg-slate-950 rounded p-2"><div className="text-slate-500">RSI clearance</div><div className="font-bold text-fuchsia-300">CALL &gt; 62 · PUT &lt; 38</div></div>
            <div className="bg-slate-950 rounded p-2"><div className="text-slate-500">Range regime</div><div className="font-bold text-fuchsia-300">PD range / ATR &lt; 8</div></div>
            <div className="bg-slate-950 rounded p-2"><div className="text-slate-500">Initial stop</div><div className="font-bold text-rose-300">1.5 × ATR</div></div>
            <div className="bg-slate-950 rounded p-2"><div className="text-slate-500">T1 / runner</div><div className="font-bold text-emerald-300">+1.5R 50% → BE · EMA9/R2/S2</div></div>
            <div className="bg-slate-950 rounded p-2 col-span-2"><div className="text-slate-500">Candidate</div><div className="font-mono text-fuchsia-300 truncate">{strategyD?.candidate_id || "--"}</div></div>
          </div>
          {strategyD?.candidate_spec_fingerprint && <div className="text-[9px] text-slate-600 font-mono break-all">spec {strategyD.candidate_spec_fingerprint}</div>}
        </div>
      </div>
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
              Strategies B and D use the premium-cap selector. Strategies A and C use delta/expiry/liquidity selection because their risk is anchored to an underlying structural stop.
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
            These shared timers primarily serve Strategy B. Strategy A V3 uses its dedicated 09:45–14:45 entry window and 15:15 forced exit shown below.
          </p>
        </div>

        <div className="bg-slate-900/90 border border-slate-800 rounded-xl p-5 shadow-lg space-y-4">
          <div className="text-sm font-bold uppercase tracking-wider text-cyan-400">Strategy A Option Execution Bands</div>
          <p className="text-[11px] text-slate-400">Strategy A V3 contract selection is delta/expiry/liquidity based, not premium-cap based.</p>
          <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
            {([
              ["preferred_delta_min","Preferred delta min",0.01,0.99,0.01],
              ["preferred_delta_max","Preferred delta max",0.01,0.99,0.01],
              ["allowed_delta_min","Allowed delta min",0.01,0.99,0.01],
              ["allowed_delta_max","Allowed delta max",0.01,0.99,0.01],
              ["minimum_expiry_sessions_remaining","Min expiry sessions",0,10,1],
              ["max_quote_age_seconds","Max quote age (sec)",1,300,1],
            ] as const).map(([key,label,min,max,step]) => (
              <label key={key} className="text-xs text-slate-400">{label}
                <input type="number" min={min} max={max} step={step} value={form.option_selection[key]}
                  onChange={e => setForm({...form, option_selection: {...form.option_selection, [key]: Number(e.target.value)}})}
                  className="block w-full mt-1 bg-slate-950 border border-slate-700 rounded p-2 text-slate-100" />
              </label>
            ))}
          </div>
        </div>

        {/* Strategy A V3 — authoritative completed-15m futures contract */}
        <div className="bg-slate-900/90 border border-cyan-900/60 rounded-xl p-5 shadow-lg space-y-4">
          <div className="flex items-center gap-2 pb-2 border-b border-slate-800 text-cyan-400 text-sm font-bold uppercase tracking-wider"><TrendingUp className="w-4 h-4" />Strategy A V3 — NIFTY Trend-Pullback Momentum</div>
          <p className="text-[11px] text-slate-400">Signal and structural-risk settings use completed 15-minute NIFTY futures bars. V3 replaces the hard ADX floor with momentum health: two-bar ADX change and directional EMA20 slope. Options remain execution-only.</p>
          <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
            {([
              ["ema_fast_period","Fast EMA",1,100,1],["ema_slow_period","Slow EMA",2,200,1],["adx_period","ADX period",1,50,1],["momentum_adx_min_delta_2bars","Min ADX 2-bar delta",-10,10,0.1],["momentum_ema20_slope_min_atr","Min directional EMA20 slope (ATR)",0,1,0.01],["momentum_ema20_slope_max_atr","Max directional EMA20 slope (ATR)",0.01,1,0.01],["atr_period","ATR period",1,50,1],
              ["ema_separation_min_atr","Min EMA separation (ATR)",0,2,0.01],["confluence_distance_atr","Confluence tolerance (ATR)",0,2,0.01],["sr_zone_atr","S/R tolerance (ATR)",0,2,0.01],
              ["confirmation_min_body_ratio","Confirmation min body ratio",0,1,0.05],["confirmation_close_location_pct","Directional close location",0,0.5,0.05],["confirmation_max_range_atr","Max confirmation range (ATR)",0.1,5,0.05],
              ["trigger_buffer_atr","Trigger buffer (ATR)",0,1,0.01],["trigger_validity_bars","Trigger validity (bars)",1,10,1],["maximum_chase_atr","Max chase (ATR)",0,2,0.01],
              ["structural_stop_buffer_atr","Structural stop buffer (ATR)",0,2,0.01],["minimum_stop_distance_atr","Minimum stop (ATR)",0.1,5,0.05],["maximum_stop_distance_atr","Maximum stop (ATR)",0.1,5,0.05],
              ["minimum_room_to_opposing_sr_r","Minimum room to S/R (R)",0.1,10,0.1],["t1_r","T1 (R)",0.1,10,0.1],["runner_target_reference_r","Runner reference (R)",0.1,10,0.1],["trailing_activation_r","Trailing activation (R)",0.1,10,0.1],
            ] as const).map(([key,label,min,max,step]) => <label key={key} className="text-xs text-slate-400">{label}<input type="number" min={min} max={max} step={step} value={form.tunables[key]} onChange={e => setForm({...form, tunables: {...form.tunables, [key]: Number(e.target.value)}})} className="block w-full mt-1 bg-slate-950 border border-slate-700 rounded p-2 text-slate-100" /></label>)}
          </div>
          <div className="grid grid-cols-3 gap-3">
            {([["entry_session_start","Entry start (IST)"],["entry_session_end","Entry end (IST)"],["forced_exit_time","Forced exit (IST)"]] as const).map(([key,label]) => <label key={key} className="text-xs text-slate-400">{label}<input type="time" value={form.tunables[key]} onChange={e => setForm({...form, tunables: {...form.tunables, [key]: e.target.value}})} className="block w-full mt-1 bg-slate-950 border border-slate-700 rounded p-2 text-slate-100" /></label>)}
          </div>
          <div className="text-[10px] text-amber-300 bg-amber-500/5 border border-amber-500/20 rounded p-2">Production Strategy A cannot bypass its entry session and cannot be force-entered. Triggered setups remain ARMED until option execution is persisted and confirmed.</div>
        </div>

        <div className="bg-slate-900/90 border border-slate-800 rounded-xl p-5 shadow-lg space-y-4">
          <div className="flex items-center gap-2 pb-2 border-b border-slate-800 text-amber-400 text-sm font-bold uppercase tracking-wider">
            <ShieldAlert className="w-4 h-4" />
            Strategy Tunables & Activation
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            {([
              ["trend_pullback_enabled", "Enable Strategy A (Trend Pullback)"],
              ["volatility_breakout_enabled", "Enable Strategy B (Volatility Breakout)"],
              ["di_continuation_enabled", "Enable Strategy C (DI Continuation)"],
              ["sr_momentum_breakout_enabled", "Enable Strategy D (S&R Momentum)"],
            ] as const).map(([key, label]) => (
              <label key={key} className="flex items-center gap-2 cursor-pointer text-xs text-slate-200">
                <input
                  type="checkbox"
                  checked={form.tunables[key]}
                  onChange={(e) =>
                    setForm({
                      ...form,
                      tunables: {
                        ...form.tunables,
                        [key]: e.target.checked,
                      },
                    })
                  }
                  className="rounded accent-cyan-500"
                />
                {label}
              </label>
            ))}
          </div>

          <div className="rounded-lg border border-rose-900/50 bg-rose-950/20 p-3 space-y-2">
            <div className="text-[10px] font-bold uppercase tracking-wider text-rose-300">
              Explicit live-routing authorization
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              {([
                ["di_continuation_live_enabled", "Allow Strategy C real orders"],
                ["sr_momentum_breakout_live_enabled", "Allow Strategy D real orders"],
              ] as const).map(([key, label]) => (
                <label key={key} className="flex items-center gap-2 cursor-pointer text-xs text-slate-200">
                  <input
                    type="checkbox"
                    checked={form.tunables[key]}
                    onChange={(e) =>
                      setForm({
                        ...form,
                        tunables: {
                          ...form.tunables,
                          [key]: e.target.checked,
                        },
                      })
                    }
                    className="rounded accent-rose-500"
                  />
                  {label}
                </label>
              ))}
            </div>
            <p className="text-[10px] text-slate-500">
              These switches are independent of signal enablement. Real orders require the strategy live switch, global LIVE mode, platform live permission, and an armed system.
            </p>
          </div>

          <div className="grid grid-cols-3 gap-3">
            <div>
              <label className="text-xs text-slate-400 block mb-1">Legacy ADX Compatibility</label>
              <input
                type="number"
                value={form.tunables.adx_threshold}
                disabled
                className="w-full bg-slate-950 border border-slate-800 rounded px-3 py-1.5 text-xs text-slate-500 cursor-not-allowed"
              />
              <p className="text-[10px] text-slate-500 mt-1">Retained for persisted V2 compatibility; not an active Strategy A V3 entry gate.</p>
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
