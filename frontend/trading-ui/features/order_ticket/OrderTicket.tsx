"use client";

import React, { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ArrowDownRight, ArrowUpRight, Check, Send } from "lucide-react";
import { createOrder } from "@/lib/api";
import { useTradingStore } from "@/stores/useTradingStore";

export function OrderTicket() {
  const queryClient = useQueryClient();
  const { orderDraft, setOrderDraft, tradingMode } = useTradingStore();
  const [lots, setLots] = useState(1);
  const [feedback, setFeedback] = useState<{ type: "success" | "error"; message: string } | null>(null);

  const mutation = useMutation({
    mutationFn: createOrder,
    onSuccess: (data) => {
      setFeedback({ type: "success", message: `Order ${data.client_order_id} placed (${data.status})` });
      queryClient.invalidateQueries({ queryKey: ["orders"] });
      queryClient.invalidateQueries({ queryKey: ["positions"] });
      queryClient.invalidateQueries({ queryKey: ["pnl_summary"] });
      setTimeout(() => setFeedback(null), 4000);
    },
    onError: (err: any) => {
      setFeedback({ type: "error", message: err.message || "Failed to place order" });
      setTimeout(() => setFeedback(null), 4000);
    },
  });

  const totalQuantity = lots * (orderDraft.lot_size || 25);
  const totalValue = totalQuantity * orderDraft.price;

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setFeedback(null);
    mutation.mutate({
      instrument_id: orderDraft.instrument_id,
      symbol: orderDraft.symbol,
      side: orderDraft.side,
      order_type: orderDraft.order_type,
      quantity: totalQuantity,
      price: orderDraft.price,
      trading_mode: tradingMode,
    });
  };

  return (
    <div className="bg-[#0b1120] border-t border-[#1e293b] p-3 text-xs select-none">
      <div className="flex items-center justify-between pb-2 border-b border-slate-800/80 mb-2">
        <span className="font-semibold text-slate-300 uppercase tracking-wider text-[11px]">
          Order Ticket
        </span>
        <span className="font-mono text-slate-400 text-[10px]">
          Mode: <strong className="text-blue-400">{tradingMode}</strong>
        </span>
      </div>

      <form onSubmit={handleSubmit} className="space-y-3">
        {/* Symbol Display */}
        <div className="flex items-center justify-between bg-slate-900/60 p-2 rounded border border-slate-800">
          <div>
            <div className="font-bold text-slate-100">{orderDraft.symbol}</div>
            <div className="text-[10px] text-slate-500 font-mono">Lot Size: {orderDraft.lot_size || 25}</div>
          </div>
          <div className="text-right">
            <div className="text-slate-400 text-[10px]">Estimated Value</div>
            <div className="font-mono font-bold text-slate-200">₹{totalValue.toFixed(2)}</div>
          </div>
        </div>

        {/* Side Selector (BUY / SELL) */}
        <div className="grid grid-cols-2 gap-2">
          <button
            type="button"
            onClick={() => setOrderDraft({ side: "BUY" })}
            className={`py-1.5 rounded font-bold flex items-center justify-center space-x-1 transition ${
              orderDraft.side === "BUY"
                ? "bg-emerald-600 text-white shadow"
                : "bg-slate-900 text-slate-400 hover:text-slate-200 border border-slate-800"
            }`}
          >
            <ArrowUpRight className="w-3.5 h-3.5" />
            <span>BUY</span>
          </button>
          <button
            type="button"
            onClick={() => setOrderDraft({ side: "SELL" })}
            className={`py-1.5 rounded font-bold flex items-center justify-center space-x-1 transition ${
              orderDraft.side === "SELL"
                ? "bg-rose-600 text-white shadow"
                : "bg-slate-900 text-slate-400 hover:text-slate-200 border border-slate-800"
            }`}
          >
            <ArrowDownRight className="w-3.5 h-3.5" />
            <span>SELL</span>
          </button>
        </div>

        {/* Inputs Grid */}
        <div className="grid grid-cols-3 gap-2 font-mono">
          {/* Order Type */}
          <div>
            <label className="block text-slate-500 text-[10px] mb-1">TYPE</label>
            <select
              value={orderDraft.order_type}
              onChange={(e) => setOrderDraft({ order_type: e.target.value as any })}
              className="w-full bg-slate-900 border border-slate-800 rounded px-2 py-1 text-slate-200 focus:outline-none"
            >
              <option value="LIMIT">LIMIT</option>
              <option value="MARKET">MARKET</option>
            </select>
          </div>

          {/* Lots */}
          <div>
            <label className="block text-slate-500 text-[10px] mb-1">LOTS</label>
            <input
              type="number"
              min={1}
              max={100}
              value={lots}
              onChange={(e) => setLots(Math.max(1, parseInt(e.target.value) || 1))}
              className="w-full bg-slate-900 border border-slate-800 rounded px-2 py-1 text-slate-200 focus:outline-none"
            />
          </div>

          {/* Price */}
          <div>
            <label className="block text-slate-500 text-[10px] mb-1">PRICE (₹)</label>
            <input
              type="number"
              step={0.05}
              disabled={orderDraft.order_type === "MARKET"}
              value={orderDraft.price}
              onChange={(e) => setOrderDraft({ price: parseFloat(e.target.value) || 0 })}
              className="w-full bg-slate-900 border border-slate-800 rounded px-2 py-1 text-slate-200 focus:outline-none disabled:opacity-50"
            />
          </div>
        </div>

        {/* Feedback Alert */}
        {feedback && (
          <div
            className={`p-2 rounded text-[11px] font-medium border ${
              feedback.type === "success"
                ? "bg-emerald-950/40 text-emerald-300 border-emerald-800"
                : "bg-rose-950/40 text-rose-300 border-rose-800"
            }`}
          >
            {feedback.message}
          </div>
        )}

        {/* Submit Button */}
        <button
          type="submit"
          disabled={mutation.isPending}
          className={`w-full py-2 rounded font-bold tracking-wider text-xs flex items-center justify-center space-x-1.5 text-white transition active:scale-[0.98] ${
            orderDraft.side === "BUY" ? "bg-emerald-600 hover:bg-emerald-500" : "bg-rose-600 hover:bg-rose-500"
          } ${mutation.isPending ? "opacity-50 cursor-not-allowed" : ""}`}
        >
          <Send className="w-3.5 h-3.5" />
          <span>
            {mutation.isPending
              ? "DISPATCHING..."
              : `SUBMIT ${orderDraft.side} (${totalQuantity} QTY)`}
          </span>
        </button>
      </form>
    </div>
  );
}

