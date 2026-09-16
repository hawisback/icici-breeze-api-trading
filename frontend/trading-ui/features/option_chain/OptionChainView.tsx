"use client";

import React, { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ChevronDown } from "lucide-react";
import { fetchOptionChain } from "@/lib/api";
import { useTradingStore } from "@/stores/useTradingStore";

export function OptionChainView() {
  const [selectedExpiry, setSelectedExpiry] = useState<string | undefined>(undefined);
  const { setOrderDraft } = useTradingStore();

  const { data: chain, isLoading } = useQuery({
    queryKey: ["option_chain", "NIFTY", selectedExpiry],
    queryFn: () => fetchOptionChain("NIFTY", selectedExpiry),
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

  return (
    <div className="flex flex-col h-full bg-[#0b1120] border-l border-[#1e293b] select-none text-xs">
      {/* Option Chain Header */}
      <div className="p-2.5 border-b border-[#1e293b] flex items-center justify-between">
        <div>
          <span className="font-semibold text-slate-200 uppercase tracking-wider text-[11px]">
            Option Chain
          </span>
          <div className="text-[10px] text-slate-500 font-mono">
            Spot: ₹{chain?.spot_price ? chain.spot_price.toFixed(2) : "24,850.50"}
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
            const isAtm = Math.abs(s.strike - (chain.spot_price || 24850)) < 50;

            return (
              <div
                key={s.strike}
                className={`grid grid-cols-7 py-1 px-2 items-center text-center transition ${
                  isAtm ? "bg-blue-950/30 font-bold" : "hover:bg-slate-900/40"
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
                      title="Click to trade Call"
                    >
                      ₹{s.call.ltp.toFixed(2)}
                    </div>
                  </>
                ) : (
                  <>
                    <div className="col-span-3 text-slate-600">-</div>
                  </>
                )}

                {/* STRIKE */}
                <div className={`font-bold ${isAtm ? "text-amber-300" : "text-slate-200"}`}>
                  {s.strike}
                </div>

                {/* PUT DATA */}
                {s.put ? (
                  <>
                    <div
                      onClick={() => handleSelectOption(s.put!, "BUY")}
                      className="cursor-pointer text-rose-400 hover:bg-rose-950/50 rounded px-1 py-0.5"
                      title="Click to trade Put"
                    >
                      ₹{s.put.ltp.toFixed(2)}
                    </div>
                    <div className="text-slate-500 text-[10px]">{(s.put.volume / 1000).toFixed(0)}k</div>
                    <div className="text-slate-500 text-[10px]">{(s.put.open_interest / 1000).toFixed(0)}k</div>
                  </>
                ) : (
                  <>
                    <div className="col-span-3 text-slate-600">-</div>
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

