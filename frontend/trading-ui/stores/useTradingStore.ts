import { create } from "zustand";

export interface OrderDraft {
  instrument_id: string;
  symbol: string;
  side: "BUY" | "SELL";
  order_type: "LIMIT" | "MARKET";
  quantity: number;
  price: number;
  lot_size: number;
}

interface TradingStore {
  selectedSymbol: string;
  selectedInstrumentId: string;
  chartInterval: string;
  tradingMode: "PAPER" | "SHADOW" | "LIVE";
  safetyMode: "NORMAL" | "ENTRY_BLOCKED" | "EXIT_ONLY" | "HALTED";
  activeTab: "positions" | "orders" | "trades" | "audit" | "system";
  orderDraft: OrderDraft;
  killSwitchModalOpen: boolean;

  setSelectedSymbol: (symbol: string, instrumentId: string) => void;
  setChartInterval: (interval: string) => void;
  setTradingMode: (mode: "PAPER" | "SHADOW" | "LIVE") => void;
  setSafetyMode: (mode: "NORMAL" | "ENTRY_BLOCKED" | "EXIT_ONLY" | "HALTED") => void;
  setActiveTab: (tab: "positions" | "orders" | "trades" | "audit" | "system") => void;
  setOrderDraft: (draft: Partial<OrderDraft>) => void;
  setKillSwitchModalOpen: (open: boolean) => void;
}

export const useTradingStore = create<TradingStore>((set) => ({
  selectedSymbol: "NIFTY 50",
  selectedInstrumentId: "INST-NIFTY-INDEX",
  chartInterval: "5m",
  tradingMode: "PAPER",
  safetyMode: "NORMAL",
  activeTab: "positions",
  orderDraft: {
    instrument_id: "INST-NIFTY-2026-09-24-24800-CE",
    symbol: "NIFTY24800CE",
    side: "BUY",
    order_type: "LIMIT",
    quantity: 25,
    price: 125.0,
    lot_size: 25,
  },
  killSwitchModalOpen: false,

  setSelectedSymbol: (symbol, instrumentId) => set({ selectedSymbol: symbol, selectedInstrumentId: instrumentId }),
  setChartInterval: (chartInterval) => set({ chartInterval }),
  setTradingMode: (tradingMode) => set({ tradingMode }),
  setSafetyMode: (safetyMode) => set({ safetyMode }),
  setActiveTab: (activeTab) => set({ activeTab }),
  setOrderDraft: (draft) => set((state) => ({ orderDraft: { ...state.orderDraft, ...draft } })),
  setKillSwitchModalOpen: (killSwitchModalOpen) => set({ killSwitchModalOpen }),
}));

