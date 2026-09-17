"use client";

import React, { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ChevronDown } from "lucide-react";
import { fetchOptionChain } from "@/lib/api";
import { useTradingStore } from "@/stores/useTradingStore";

export function OptionChainView() {
  const [selectedExpiry, setSelectedExpiry] = useState<string | undefined>(undefined);
  const { selectedSymbol, setOrderDraft } = useTradingStore();

  const underlying = selectedSymbol.includes("BANK") ? "BANKNIFTY" : "NIFTY";

  const { data: chain, isLoading } = useQuery({
    queryKey: ["option_chain", underlying, selectedExpiry],
    queryFn: () => fetchOptionChain(underlying, selectedExpiry),
    refetchInterval: 3000,
  });

  const handleSelectOption = (
    opt: { instrument_id: string; symbol: string; ltp: number; lot_size: number },
    side: "BUY" | "SELL"
  ) => {
    setOrderDraft({
      instrument_id: opt.instrument_id,
      symbol: opt.symbol,
      side: side,
      price: opt.ltp,
      quantity: opt.lot_size,
      lot_size: opt.lot_size,
    });
  };

  const atmStrike = chain?.atm_strike || (chain?.spot_price ? Math.round(chain.spot_price / (underlying === "BANKNIFTY" ? 100 : 50)) * (underlying === "BANKNIFTY" ? 100 : 50) : 0);

  return (
    <div className="flex flex-col h-full bg-[#0b1120] border-l border-[#1e293b] select-none text-xs">
      {/* Option Chain Header */}
      <div className="p-2.5 border-b border-[#1e293b] flex items-center justify-between">
        <div>
          <div className="flex items-center space-x-2">
            <span className="font-semibold text-slate-200 uppercase tracking-wider text-[11px]">
              {underlying} Option Chain
            </span>
            {chain?.source === "BREEZE" ? (
              <span className="px-1.5 py-0.2 rounded bg-emerald-950 border border-emerald-800 text-emerald-300 font-mono text-[9px]">
                BREEZE LIVE
              </span>
            ) : (
              <span className="px-1.5 py-0.2 rounded bg-slate-800 border border-slate-700 text-slate-400 font-mono text-[9px]">
                SYNTHETIC (OFFLINE)
              </span>
            )}
          </div>
          <div className="text-[10px] text-slate-400 font-mono mt-0.5">
            Spot: <span className="text-slate-100 font-bold">₹{chain?.spot_price ? chain.spot_price.toFixed(2) : "--"}</span>
            {atmStrike > 0 && <span className="text-amber-400 ml-2">ATM: ₹{atmStrike}</span>}
          </div>
        </div>

        {/* Expiry Selector */}
        {chain?.available_expiries && chain.available_expiries.length > 0 && (
          <select
            value={selectedExpiry || chain.expiry}
            onChange={(e) => setSelectedExpiry(e.target.value)}
            className="px-2 py-1 bg-slate-900 border border-slate-800 rounded text-slate-300 font-mono text-[10px] focus:outline-none"
          >
            {chain.available_expiries.map((exp) => (
              <option key={exp} value={exp}>
                {exp}
              </option>
            ))}
          </select>
        )}
      </div>

      {/* Table Header */}
      <div className="grid grid-cols-7 bg-slate-900/80 py-1.5 px-2 border-b border-slate-800 text-[10px] font-mono text-slate-400 text-center">
        <div className="col-span-3 text-emerald-400 font-bold">CALLS (CE)</div>
        <div className="col-span-1 text-slate-300 font-bold">STRIKE</div>
        <div className="col-span-3 text-rose-400 font-bold">PUTS (PE)</div>
      </div>
      <div className="grid grid-cols-7 bg-slate-900/40 py-1 px-2 border-b border-slate-800 text-[9px] font-mono text-slate-500 text-center">
        <div>OI</div>
        <div>VOL</div>
        <div>LTP</div>
        <div className="text-slate-400 font-semibold">₹</div>
        <div>LTP</div>
        <div>VOL</div>
        <div>OI</div>
      </div>

      {/* Strikes Matrix */}
      <div className="flex-1 overflow-y-auto divide-y divide-slate-800/40 text-[11px] font-mono">
        {isLoading ? (
          <div className="p-6 text-center text-slate-500">Loading option matrix...</div>
        ) : (
          chain?.strikes.map((s) => {
            const isAtm = s.strike === atmStrike;

            return (
              <div
                key={s.strike}
                className={`grid grid-cols-7 py-1 px-2 items-center text-center transition ${
                  isAtm ? "bg-amber-950/30 border-y border-amber-500/40 font-bold" : "hover:bg-slate-900/40"
                }`}
              >
                {/* CALL DATA */}
                {s.call ? (
                  <>
                    <div className="text-slate-500 text-[10px]">{(s.call.open_interest / 1000).toFixed(0)}k</div>
                    <div className="text-slate-500 text-[10px]">{(s.call.volume / 1000).toFixed(0)}k</div>
                    <div
                      onClick={() => handleSelectOption(s.call!, "BUY")}
                      className="cursor-pointer text-emerald-400 hover:bg-emerald-950/50 rounded px-1 py-0.5"
                    >
                      {s.call.ltp.toFixed(2)}
                    </div>
                  </>
                ) : (
                  <>
                    <div className="text-slate-700">-</div>
                    <div className="text-slate-700">-</div>
                    <div className="text-slate-700">-</div>
                  </>
                )}

                {/* STRIKE */}
                <div
                  className={`py-0.5 rounded text-[11px] ${
                    isAtm ? "bg-amber-500/20 text-amber-300 font-bold" : "text-slate-200"
                  }`}
                >
                  {s.strike}
                  {isAtm && <span className="block text-[8px] text-amber-400">ATM</span>}
                </div>

                {/* PUT DATA */}
                {s.put ? (
                  <>
                    <div
                      onClick={() => handleSelectOption(s.put!, "BUY")}
                      className="cursor-pointer text-rose-400 hover:bg-rose-950/50 rounded px-1 py-0.5"
                    >
                      {s.put.ltp.toFixed(2)}
                    </div>
                    <div className="text-slate-500 text-[10px]">{(s.put.volume / 1000).toFixed(0)}k</div>
                    <div className="text-slate-500 text-[10px]">{(s.put.open_interest / 1000).toFixed(0)}k</div>
                  </>
                ) : (
                  <>
                    <div className="text-slate-700">-</div>
                    <div className="text-slate-700">-</div>
                    <div className="text-slate-700">-</div>
                  </>
                )}
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}
