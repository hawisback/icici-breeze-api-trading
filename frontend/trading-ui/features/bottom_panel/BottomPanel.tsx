"use client";

import React from "react";
import { useQuery } from "@tanstack/react-query";
import {
  fetchAuditLogs,
  fetchOrders,
  fetchPositions,
  fetchSystemHealth,
} from "@/lib/api";
import { useTradingStore } from "@/stores/useTradingStore";
import { formatISTTime } from "@/lib/time";

export function BottomPanel() {
  const { activeTab, setActiveTab } = useTradingStore();

  const { data: positions } = useQuery({
    queryKey: ["positions"],
    queryFn: fetchPositions,
    refetchInterval: 3000,
  });

  const { data: orders } = useQuery({
    queryKey: ["orders"],
    queryFn: fetchOrders,
    refetchInterval: 3000,
  });

  const { data: auditLogs } = useQuery({
    queryKey: ["audit_logs"],
    queryFn: fetchAuditLogs,
    refetchInterval: 4000,
  });

  const { data: health } = useQuery({
    queryKey: ["system_health"],
    queryFn: fetchSystemHealth,
    refetchInterval: 5000,
  });

  return (
    <div className="flex flex-col h-full bg-[#0a0e17] border-t border-[#1e293b] select-none text-xs">
      {/* Tabs Header */}
      <div className="flex items-center space-x-1 px-3 border-b border-[#1e293b] bg-[#0f172a]">
        {[
          { id: "positions", label: `Positions (${positions?.length || 0})` },
          { id: "orders", label: `Orders (${orders?.length || 0})` },
          { id: "audit", label: "Audit Trail" },
          { id: "system", label: "System Health" },
        ].map((tab) => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id as any)}
            className={`px-3 py-2 font-medium tracking-wide border-b-2 transition ${
              activeTab === tab.id
                ? "border-blue-500 text-blue-400 font-bold"
                : "border-transparent text-slate-400 hover:text-slate-200"
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* Tab Content */}
      <div className="flex-1 overflow-auto p-2 font-mono">
        {/* POSITIONS TAB */}
        {activeTab === "positions" && (
          <table className="w-full text-left text-[11px] divide-y divide-slate-800">
            <thead>
              <tr className="text-slate-500 text-[10px]">
                <th className="py-1">SYMBOL</th>
                <th className="py-1">QTY</th>
                <th className="py-1">AVG PRICE</th>
                <th className="py-1">LTP</th>
                <th className="py-1">REALIZED P&L</th>
                <th className="py-1">UNREALIZED P&L</th>
                <th className="py-1">TOTAL P&L</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800/60">
              {!positions || positions.length === 0 ? (
                <tr>
                  <td colSpan={7} className="py-4 text-center text-slate-600">
                    No open positions
                  </td>
                </tr>
              ) : (
                positions.map((p) => {
                  const isPos = p.total_pnl >= 0;
                  return (
                    <tr key={p.position_id} className="hover:bg-slate-900/40">
                      <td className="py-1.5 font-bold text-slate-200">{p.symbol}</td>
                      <td className={`py-1.5 ${p.quantity >= 0 ? "text-emerald-400" : "text-rose-400"}`}>
                        {p.quantity}
                      </td>
                      <td className="py-1.5 text-slate-300">₹{p.average_price.toFixed(2)}</td>
                      <td className="py-1.5 text-slate-300">₹{p.current_price.toFixed(2)}</td>
                      <td className="py-1.5 text-slate-400">₹{p.realized_pnl.toFixed(2)}</td>
                      <td className={`py-1.5 ${p.unrealized_pnl >= 0 ? "text-emerald-400" : "text-rose-400"}`}>
                        ₹{p.unrealized_pnl.toFixed(2)}
                      </td>
                      <td className={`py-1.5 font-bold ${isPos ? "text-emerald-400" : "text-rose-400"}`}>
                        ₹{p.total_pnl.toFixed(2)}
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        )}

        {/* ORDERS TAB */}
        {activeTab === "orders" && (
          <table className="w-full text-left text-[11px] divide-y divide-slate-800">
            <thead>
              <tr className="text-slate-500 text-[10px]">
                <th className="py-1">TIME</th>
                <th className="py-1">CLIENT ID</th>
                <th className="py-1">SYMBOL</th>
                <th className="py-1">SIDE</th>
                <th className="py-1">TYPE</th>
                <th className="py-1">QTY</th>
                <th className="py-1">PRICE</th>
                <th className="py-1">STATUS</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800/60">
              {!orders || orders.length === 0 ? (
                <tr>
                  <td colSpan={8} className="py-4 text-center text-slate-600">
                    No orders submitted
                  </td>
                </tr>
              ) : (
                orders.map((o) => (
                  <tr key={o.order_id} className="hover:bg-slate-900/40">
                    <td className="py-1.5 text-slate-500">
                      {formatISTTime(o.created_at, { second: "2-digit" })}
                    </td>
                    <td className="py-1.5 text-slate-400">{o.client_order_id}</td>
                    <td className="py-1.5 font-bold text-slate-200">{o.symbol}</td>
                    <td className={`py-1.5 font-bold ${o.side === "BUY" ? "text-emerald-400" : "text-rose-400"}`}>
                      {o.side}
                    </td>
                    <td className="py-1.5 text-slate-400">{o.order_type}</td>
                    <td className="py-1.5 text-slate-200">
                      {o.filled_quantity}/{o.quantity}
                    </td>
                    <td className="py-1.5 text-slate-300">₹{o.price.toFixed(2)}</td>
                    <td className="py-1.5">
                      <span
                        className={`px-1.5 py-0.5 rounded text-[10px] font-bold ${
                          o.status === "FILLED"
                            ? "bg-emerald-950 text-emerald-400 border border-emerald-800"
                            : o.status === "RISK_REJECTED" || o.status === "REJECTED"
                            ? "bg-rose-950 text-rose-400 border border-rose-800"
                            : o.status === "SUBMISSION_UNKNOWN"
                            ? "bg-amber-950 text-amber-400 border border-amber-800 animate-pulse"
                            : "bg-blue-950 text-blue-400 border border-blue-800"
                        }`}
                      >
                        {o.status}
                      </span>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        )}

        {/* AUDIT TRAIL TAB */}
        {activeTab === "audit" && (
          <div className="space-y-1 divide-y divide-slate-800/60">
            {!auditLogs || auditLogs.length === 0 ? (
              <div className="py-4 text-center text-slate-600">No audit events recorded</div>
            ) : (
              auditLogs.map((log) => (
                <div key={log.event_id} className="pt-1.5 flex items-center justify-between text-[10px]">
                  <div className="flex items-center space-x-2">
                    <span className="text-slate-500">{formatISTTime(log.occurred_at, { second: "2-digit" })}</span>
                    <span className="font-bold text-blue-400">{log.event_type}</span>
                    <span className="text-slate-500">[{log.source}]</span>
                  </div>
                  <div className="text-slate-400 truncate max-w-lg font-mono text-[9px]">
                    {JSON.stringify(log.payload)}
                  </div>
                </div>
              ))
            )}
          </div>
        )}

        {/* SYSTEM HEALTH TAB */}
        {activeTab === "system" && (
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 p-2">
            {health?.services &&
              Object.entries(health.services).map(([key, val]) => (
                <div key={key} className="bg-slate-900/80 p-2.5 rounded border border-slate-800">
                  <div className="text-[10px] text-slate-400 uppercase tracking-wider">{key.replace("_", " ")}</div>
                  <div
                    className={`mt-1 font-bold text-xs ${
                      val === "ONLINE" || val === "CONNECTED" || val === "LIVE" || val === "ACTIVE" || val === "NORMAL"
                        ? "text-emerald-400"
                        : "text-amber-400"
                    }`}
                  >
                    {val}
                  </div>
                </div>
              ))}
            {health?.broker_sessions &&
              Object.entries(health.broker_sessions).map(([broker, status]) => (
                <div key={`broker-${broker}`} className="bg-slate-900/80 p-2.5 rounded border border-slate-800">
                  <div className="text-[10px] text-slate-400 uppercase tracking-wider">
                    {broker} session
                  </div>
                  <div className={`mt-1 font-bold text-xs ${
                    status === "CONNECTED" ? "text-emerald-400" : "text-amber-400"
                  }`}>
                    {status}
                  </div>
                </div>
              ))}
            {health?.config?.live_execution_broker && (
              <div className="bg-slate-900/80 p-2.5 rounded border border-rose-900/50">
                <div className="text-[10px] text-slate-400 uppercase tracking-wider">LIVE execution</div>
                <div className="mt-1 font-bold text-xs text-rose-300">
                  {String(health.config.live_execution_broker).toUpperCase()}
                </div>
              </div>
            )}
            {health?.config?.frequent_data_broker && (
              <div className="bg-slate-900/80 p-2.5 rounded border border-sky-900/50">
                <div className="text-[10px] text-slate-400 uppercase tracking-wider">Frequent data</div>
                <div className="mt-1 font-bold text-xs text-sky-300">
                  {String(health.config.frequent_data_broker).toUpperCase()}
                </div>
              </div>
            )}
            {health?.config?.reference_data_broker && (
              <div className="bg-slate-900/80 p-2.5 rounded border border-violet-900/50">
                <div className="text-[10px] text-slate-400 uppercase tracking-wider">Reference data</div>
                <div className="mt-1 font-bold text-xs text-violet-300">
                  {String(health.config.reference_data_broker).toUpperCase()}
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

