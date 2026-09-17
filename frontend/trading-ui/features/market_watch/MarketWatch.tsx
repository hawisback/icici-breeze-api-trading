"use client";

import React, { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Search, TrendingDown, TrendingUp } from "lucide-react";
import { fetchQuotes } from "@/lib/api";
import { useTradingStore } from "@/stores/useTradingStore";

export function MarketWatch() {
  const [searchQuery, setSearchQuery] = useState("");
  const { selectedSymbol, setSelectedSymbol, setOrderDraft } = useTradingStore();

  const { data: quotes, isLoading } = useQuery({
    queryKey: ["quotes"],
    queryFn: fetchQuotes,
    refetchInterval: 1000,
  });

  const filteredQuotes = (quotes || []).filter((q) =>
    q.symbol.toLowerCase().includes(searchQuery.toLowerCase())
  );

  return (
    <div className="flex flex-col h-full bg-[#0b1120] border-r border-[#1e293b] select-none text-xs">
      {/* Header & Search */}
      <div className="p-2.5 border-b border-[#1e293b]">
        <div className="flex items-center justify-between pb-2">
          <span className="font-semibold text-slate-300 uppercase tracking-wider text-[11px]">
            Market Watch
          </span>
          <span className="text-[10px] text-slate-500 font-mono">
            {filteredQuotes.length} items
          </span>
        </div>
        <div className="relative">
          <Search className="w-3.5 h-3.5 absolute left-2.5 top-2 text-slate-500" />
          <input
            type="text"
            placeholder="Search index or contract..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="w-full pl-8 pr-3 py-1 bg-slate-900 border border-slate-800 rounded text-slate-200 placeholder-slate-500 focus:outline-none focus:border-blue-500"
          />
        </div>
      </div>

      {/* Items List */}
      <div className="flex-1 overflow-y-auto divide-y divide-slate-800/60">
        {isLoading ? (
          <div className="p-4 text-center text-slate-500 font-mono">Loading quotes...</div>
        ) : filteredQuotes.length === 0 ? (
          <div className="p-4 text-center text-slate-500 font-mono">No contracts found</div>
        ) : (
          filteredQuotes.map((q) => {
            const isSelected = selectedSymbol === q.symbol;
            const isPositive = q.change_pct >= 0;

            return (
              <div
                key={q.instrument_id}
                onClick={() => {
                  setSelectedSymbol(q.symbol, q.instrument_id);
                  setOrderDraft({
                    symbol: q.symbol,
                    instrument_id: q.instrument_id,
                    price: q.last_price,
                  });
                }}
                className={`p-2.5 flex items-center justify-between cursor-pointer transition ${
                  isSelected
                    ? "bg-blue-950/40 border-l-2 border-blue-500"
                    : "hover:bg-slate-900/60"
                }`}
              >
                <div>
                  <div className="font-semibold text-slate-200">{q.symbol}</div>
                  <div className="text-[10px] text-slate-500 font-mono">Vol: {q.volume.toLocaleString()}</div>
                </div>

                <div className="text-right">
                  <div className="font-mono font-medium text-slate-100">
                    ₹{q.last_price.toFixed(2)}
                  </div>
                  <div
                    className={`flex items-center justify-end text-[10px] font-mono ${
                      isPositive ? "text-emerald-400" : "text-rose-400"
                    }`}
                  >
                    {isPositive ? (
                      <TrendingUp className="w-3 h-3 mr-0.5" />
                    ) : (
                      <TrendingDown className="w-3 h-3 mr-0.5" />
                    )}
                    {isPositive ? "+" : ""}
                    {q.change_pct.toFixed(2)}%
                  </div>
                </div>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}

