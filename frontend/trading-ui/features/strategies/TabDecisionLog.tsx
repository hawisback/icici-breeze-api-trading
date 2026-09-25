"use client";

import React, { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ChevronDown, ChevronRight, FileText, Filter, RefreshCw, Search } from "lucide-react";
import { DecisionLogData, fetchStrategyDecisionLog } from "../../lib/api";
import { formatISTTime } from "../../lib/time";

export const TabDecisionLog: React.FC = () => {
  const [filterCategory, setFilterCategory] = useState<string>("ALL");
  const [searchQuery, setSearchQuery] = useState<string>("");
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const {
    data: logs = [],
    isLoading,
    refetch: loadLogs,
  } = useQuery<DecisionLogData[]>({
    queryKey: ["strategy_decision_log"],
    queryFn: () => fetchStrategyDecisionLog(100),
    refetchInterval: 2500,
  });

  const getCategoryBadgeClass = (category: string) => {
    switch (category) {
      case "ORDER":
        return "bg-emerald-500/20 text-emerald-300 border-emerald-500/30";
      case "EXIT":
        return "bg-rose-500/20 text-rose-300 border-rose-500/30";
      case "SETUP":
        return "bg-indigo-500/20 text-indigo-300 border-indigo-500/30";
      case "CONTRACT_SELECTION":
        return "bg-cyan-500/20 text-cyan-300 border-cyan-500/30";
      case "RISK":
        return "bg-amber-500/20 text-amber-300 border-amber-500/30";
      case "SECURITY":
      case "EMERGENCY":
        return "bg-purple-500/20 text-purple-300 border-purple-500/30";
      default:
        return "bg-slate-800 text-slate-300 border-slate-700";
    }
  };

  const filteredLogs = logs.filter((log) => {
    if (filterCategory !== "ALL" && log.category !== filterCategory) return false;
    if (searchQuery.trim() !== "") {
      const q = searchQuery.toLowerCase();
      return (
        log.message.toLowerCase().includes(q) ||
        (log.strategy && log.strategy.toLowerCase().includes(q)) ||
        log.category.toLowerCase().includes(q)
      );
    }
    return true;
  });

  return (
    <div className="space-y-4">
      {/* Controls */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 pb-3 border-b border-slate-800">
        <div>
          <h3 className="text-base font-bold text-slate-100 flex items-center gap-2">
            <FileText className="w-4 h-4 text-cyan-400" />
            Algorithmic Decision & Audit Log
          </h3>
          <p className="text-xs text-slate-400">
            Real-time explainability record of every setup trigger, contract selection filter, stop trail adjustment, and exit action.
          </p>
        </div>

        <div className="flex items-center gap-2">
          {/* Category Filter */}
          <select
            value={filterCategory}
            onChange={(e) => setFilterCategory(e.target.value)}
            className="bg-slate-900 border border-slate-800 rounded px-2.5 py-1.5 text-xs text-slate-200"
          >
            <option value="ALL">All Categories</option>
            <option value="SETUP">Setups</option>
            <option value="CONTRACT_SELECTION">Contract Selection</option>
            <option value="ORDER">Orders & Fills</option>
            <option value="EXIT">Position Exits</option>
            <option value="RISK">Risk Decisions</option>
            <option value="SECURITY">Security / Arms</option>
          </select>

          {/* Search Box */}
          <div className="relative">
            <Search className="w-3.5 h-3.5 text-slate-400 absolute left-2.5 top-2.5" />
            <input
              type="text"
              placeholder="Search logs..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="bg-slate-900 border border-slate-800 rounded pl-8 pr-3 py-1 text-xs text-slate-100 w-44"
            />
          </div>

          <button
            onClick={() => loadLogs()}
            disabled={isLoading}
            className="p-1.5 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded text-xs transition-colors"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${isLoading ? "animate-spin" : ""}`} />
          </button>
        </div>
      </div>

      {/* Log Feed */}
      <div className="bg-slate-900/90 border border-slate-800 rounded-xl overflow-hidden shadow-lg">
        {filteredLogs.length === 0 ? (
          <div className="p-12 text-center text-slate-500 text-xs">
            No decision logs match the active filter criteria.
          </div>
        ) : (
          <div className="divide-y divide-slate-800/80">
            {filteredLogs.map((log) => {
              const isExpanded = expandedId === log.id;
              const hasDetails = log.details && Object.keys(log.details).length > 0;

              return (
                <div
                  key={log.id}
                  className="p-3.5 hover:bg-slate-850/50 transition-colors text-xs font-mono"
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="flex items-start gap-2.5 flex-1">
                      <span className="text-slate-500 whitespace-nowrap mt-0.5">
                        {formatISTTime(log.timestamp, { second: "2-digit" })}
                      </span>

                      <span
                        className={`px-2 py-0.5 rounded border text-[10px] font-bold tracking-wider ${getCategoryBadgeClass(
                          log.category
                        )}`}
                      >
                        {log.category}
                      </span>

                      {log.strategy && (
                        <span className="px-1.5 py-0.5 rounded bg-slate-800 text-slate-400 text-[10px]">
                          {log.strategy}
                        </span>
                      )}

                      <span className="text-slate-200 font-sans flex-1">{log.message}</span>
                    </div>

                    {hasDetails && (
                      <button
                        onClick={() => setExpandedId(isExpanded ? null : log.id)}
                        className="text-slate-400 hover:text-slate-200 flex items-center gap-1 text-[11px] font-sans"
                      >
                        {isExpanded ? (
                          <>
                            Less <ChevronDown className="w-3.5 h-3.5" />
                          </>
                        ) : (
                          <>
                            Details <ChevronRight className="w-3.5 h-3.5" />
                          </>
                        )}
                      </button>
                    )}
                  </div>

                  {isExpanded && hasDetails && (
                    <div className="mt-2.5 p-3 bg-slate-950 rounded border border-slate-800 overflow-x-auto">
                      <pre className="text-[11px] text-slate-300">
                        {JSON.stringify(log.details, null, 2)}
                      </pre>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
};

