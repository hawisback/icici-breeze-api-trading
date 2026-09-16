"use client";

import React from "react";
import { BottomPanel } from "@/features/bottom_panel/BottomPanel";
import { TradingChart } from "@/features/charts/TradingChart";
import { GlobalHeader } from "@/features/header/GlobalHeader";
import { MarketWatch } from "@/features/market_watch/MarketWatch";
import { KillSwitchModal } from "@/features/modals/KillSwitchModal";
import { OptionChainView } from "@/features/option_chain/OptionChainView";
import { OrderTicket } from "@/features/order_ticket/OrderTicket";

export default function TradingTerminalPage() {
  return (
    <div className="flex flex-col h-screen w-screen overflow-hidden bg-[#0a0e17] text-slate-100 font-sans">
      {/* 1. Global Terminal Header */}
      <GlobalHeader />

      {/* 2. Main Workspace */}
      <div className="flex flex-1 overflow-hidden">
        {/* Left: Market Watch (72) */}
        <div className="w-64 flex-shrink-0 h-full">
          <MarketWatch />
        </div>

        {/* Center: Main Candlestick & Volume Chart */}
        <div className="flex-1 h-full min-w-0 flex flex-col">
          <TradingChart />
        </div>

        {/* Right: Option Chain & Order Ticket */}
        <div className="w-[420px] flex-shrink-0 h-full flex flex-col border-l border-[#1e293b]">
          {/* Top half: Option Chain */}
          <div className="flex-1 overflow-hidden">
            <OptionChainView />
          </div>

          {/* Bottom half: Order Ticket */}
          <div className="flex-shrink-0">
            <OrderTicket />
          </div>
        </div>
      </div>

      {/* 3. Bottom Tabs: Positions, Orders, Trades, Audit, System */}
      <div className="h-64 flex-shrink-0">
        <BottomPanel />
      </div>

      {/* 4. Modals */}
      <KillSwitchModal />
    </div>
  );
}

