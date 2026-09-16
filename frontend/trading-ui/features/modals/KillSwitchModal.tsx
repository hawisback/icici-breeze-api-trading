"use client";

import React, { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Ban, Power, ShieldAlert, X } from "lucide-react";
import { triggerKillSwitch } from "@/lib/api";
import { useTradingStore } from "@/stores/useTradingStore";

export function KillSwitchModal() {
  const queryClient = useQueryClient();
  const { killSwitchModalOpen, setKillSwitchModalOpen, setSafetyMode } = useTradingStore();
  const [reason, setReason] = useState("Manual emergency trigger");

  const mutation = useMutation({
    mutationFn: (action: string) => triggerKillSwitch(action, reason),
    onSuccess: (data, action) => {
      if (action === "BLOCK_ENTRIES") setSafetyMode("ENTRY_BLOCKED");
      else if (action === "HALT") setSafetyMode("HALTED");
      else if (action === "EXIT_ONLY") setSafetyMode("EXIT_ONLY");

      queryClient.invalidateQueries({ queryKey: ["system_health"] });
      queryClient.invalidateQueries({ queryKey: ["audit_logs"] });
      setKillSwitchModalOpen(false);
    },
  });

  if (!killSwitchModalOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-sm select-none text-xs">
      <div className="bg-[#0f172a] border border-rose-600/80 rounded-lg max-w-md w-full p-4 shadow-2xl space-y-4">
        <div className="flex items-center justify-between border-b border-slate-800 pb-3">
          <div className="flex items-center space-x-2 text-rose-400 font-bold text-sm">
            <AlertTriangle className="w-5 h-5" />
            <span>EMERGENCY KILL SWITCH</span>
          </div>
          <button
            onClick={() => setKillSwitchModalOpen(false)}
            className="text-slate-400 hover:text-slate-200"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        <p className="text-slate-300 leading-relaxed text-[11px]">
          Selecting an emergency action immediately enforces server-side trading safety modes in the Risk Engine.
        </p>

        <div>
          <label className="block text-slate-400 text-[10px] mb-1 font-mono">REASON FOR INTERVENTION</label>
          <input
            type="text"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            className="w-full bg-slate-900 border border-slate-800 rounded px-3 py-1.5 text-slate-200 focus:outline-none focus:border-rose-500"
          />
        </div>

        <div className="space-y-2 pt-1">
          {/* Action 1: Block Entries */}
          <button
            onClick={() => mutation.mutate("BLOCK_ENTRIES")}
            disabled={mutation.isPending}
            className="w-full p-2.5 bg-amber-950/40 hover:bg-amber-900/60 border border-amber-600 text-amber-200 rounded flex items-center justify-between font-bold transition"
          >
            <div className="flex items-center space-x-2">
              <Ban className="w-4 h-4 text-amber-400" />
              <span>BLOCK NEW ENTRIES</span>
            </div>
            <span className="text-[10px] text-amber-400/80 font-normal">Prevents buy orders</span>
          </button>

          {/* Action 2: Exit Only */}
          <button
            onClick={() => mutation.mutate("EXIT_ONLY")}
            disabled={mutation.isPending}
            className="w-full p-2.5 bg-orange-950/40 hover:bg-orange-900/60 border border-orange-600 text-orange-200 rounded flex items-center justify-between font-bold transition"
          >
            <div className="flex items-center space-x-2">
              <ShieldAlert className="w-4 h-4 text-orange-400" />
              <span>SET EXIT ONLY</span>
            </div>
            <span className="text-[10px] text-orange-400/80 font-normal">Allows square-offs only</span>
          </button>

          {/* Action 3: Complete Halt */}
          <button
            onClick={() => mutation.mutate("HALT")}
            disabled={mutation.isPending}
            className="w-full p-2.5 bg-rose-950/50 hover:bg-rose-900/70 border border-rose-600 text-rose-200 rounded flex items-center justify-between font-bold transition"
          >
            <div className="flex items-center space-x-2">
              <Power className="w-4 h-4 text-rose-400" />
              <span>HALT ALL TRADING</span>
            </div>
            <span className="text-[10px] text-rose-400/80 font-normal">Full freeze</span>
          </button>
        </div>

        <div className="pt-2 border-t border-slate-800 text-right">
          <button
            onClick={() => setKillSwitchModalOpen(false)}
            className="px-4 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded font-medium text-xs transition"
          >
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}

