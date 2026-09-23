"use client";

import React from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Activity,
  AlertOctagon,
  CheckCircle2,
  ExternalLink,
  Lock,
  Power,
  RefreshCw,
  ShieldAlert,
  Wifi,
  WifiOff,
  X,
} from "lucide-react";
import { fetchLoginUrl, fetchPnLSummary, fetchQuotes, fetchSystemHealth } from "@/lib/api";
import { useTradingWebSocket } from "@/lib/useWebSocket";
import { useTradingStore } from "@/stores/useTradingStore";

interface GlobalHeaderProps {
  activeView?: "terminal" | "strategies";
  onViewChange?: (view: "terminal" | "strategies") => void;
}

export function GlobalHeader({ activeView = "terminal", onViewChange }: GlobalHeaderProps) {
  const { connected: wsConnected } = useTradingWebSocket();
  const {
    tradingMode,
    setTradingMode,
    safetyMode,
    setKillSwitchModalOpen,
  } = useTradingStore();

  const { data: health, refetch: refetchHealth } = useQuery({
    queryKey: ["system_health"],
    queryFn: fetchSystemHealth,
    refetchInterval: 5000,
  });

  // Listen for OAuth completion message from popup window
  React.useEffect(() => {
    const handleMessage = (e: MessageEvent) => {
      if (e.data?.type === "BREEZE_SESSION_SUCCESS" || e.data?.type === "KITE_SESSION_SUCCESS") {
        refetchHealth();
      }
    };
    window.addEventListener("message", handleMessage);
    return () => window.removeEventListener("message", handleMessage);
  }, [refetchHealth]);

  const [showAuthModal, setShowAuthModal] = React.useState(false);
  const [tokenInput, setTokenInput] = React.useState("");
  const [isSubmitting, setIsSubmitting] = React.useState(false);
  const [authError, setAuthError] = React.useState("");
  const [authSuccess, setAuthSuccess] = React.useState("");
  const brokerBackend = health?.config?.broker_backend === "kite" ? "kite" : "breeze";
  const [authBroker, setAuthBroker] = React.useState<"breeze" | "kite">("kite");
  React.useEffect(() => {
    if (!showAuthModal) setAuthBroker(brokerBackend);
  }, [brokerBackend, showAuthModal]);
  const brokerLabel = authBroker === "kite" ? "Kite" : "ICICI Breeze";

  const handleConnectBroker = async () => {
    try {
      const data = await fetchLoginUrl(authBroker);
      if (data.login_url) {
        window.open(
          data.login_url,
          `${brokerLabel}Login`,
          "width=600,height=750,menubar=no,toolbar=no,status=no,scrollbars=yes"
        );
      }
    } catch (err) {
      console.error(`Failed to initiate ${brokerLabel} login:`, err);
    }
  };

  const handleManualActivate = async (e: React.FormEvent) => {
    e.preventDefault();
    let raw = tokenInput.trim();
    if (!raw) return;

    const tokenParam = authBroker === "kite" ? "request_token" : "apisession";
    if (raw.includes(`${tokenParam}=`)) {
      const match = raw.match(new RegExp(`${tokenParam}=([a-zA-Z0-9_-]+)`));
      if (match) raw = match[1];
    }

    setIsSubmitting(true);
    setAuthError("");
    setAuthSuccess("");

    try {
      const res = await fetch(`http://127.0.0.1:8000/api/v1/broker/session/callback?broker=${authBroker}&${tokenParam}=${encodeURIComponent(raw)}`, {
        headers: { Accept: "application/json" },
      });
      const data = await res.json();
      if (data.status === "SUCCESS") {
        setAuthSuccess(`${brokerLabel} successfully authenticated and session saved to .env!`);
        await refetchHealth();
        setTimeout(() => {
          setShowAuthModal(false);
          setTokenInput("");
          setAuthSuccess("");
        }, 1200);
      } else {
        setAuthError(data.message || "Failed to authenticate session.");
      }
    } catch (err: any) {
      setAuthError(err.message || "Network error communicating with API Gateway.");
    } finally {
      setIsSubmitting(false);
    }
  };

  const { data: quotes } = useQuery({
    queryKey: ["quotes"],
    queryFn: fetchQuotes,
    refetchInterval: 2000,
  });

  const { data: pnl } = useQuery({
    queryKey: ["pnl_summary"],
    queryFn: fetchPnLSummary,
    refetchInterval: 1000,
  });

  const niftyQuote = quotes?.find(
    (q) => q.symbol === "NIFTY 50" || q.instrument_id === "INST-NIFTY-INDEX" || q.symbol === "NIFTY"
  );

  const brokerSessionStatus = health?.services?.broker_session || "DISCONNECTED";
  const breezeConnected = health?.broker_sessions?.breeze?.connected ?? (brokerBackend === "breeze" && brokerSessionStatus === "CONNECTED");
  const kiteConnected = health?.broker_sessions?.kite?.connected ?? (brokerBackend === "kite" && brokerSessionStatus === "CONNECTED");
  const connectedBrokerCount = Number(breezeConnected) + Number(kiteConnected);
  const isBrokerActive = connectedBrokerCount > 0;
  const isBrokerExpired = !isBrokerActive && brokerSessionStatus === "EXPIRED";
  const marketFeedStatus = health?.services?.market_feed || "LIVE";

  return (
    <header className="h-14 bg-[#0f172a] border-b border-[#1e293b] px-4 flex items-center justify-between select-none text-xs">
      {/* Brand & Market Ticker */}
      <div className="flex items-center space-x-6">
        <div className="flex items-center space-x-2">
          <div className="w-2.5 h-2.5 rounded-full bg-blue-500 animate-pulse" />
          <span className="font-bold tracking-wider text-sm bg-gradient-to-r from-blue-400 to-teal-300 bg-clip-text text-transparent">
            MULTI-BROKER TRADING TERMINAL
          </span>
          <span className="text-[10px] px-1.5 py-0.5 bg-slate-800 text-slate-400 rounded font-mono border border-slate-700">
            v2.0
          </span>
        </div>

        {/* Workspace View Switcher */}
        {onViewChange && (
          <div className="flex items-center bg-slate-900 border border-slate-800 rounded p-0.5 ml-2">
            <button
              onClick={() => onViewChange("terminal")}
              className={`px-3 py-1 rounded text-[11px] font-bold tracking-wider transition ${
                activeView === "terminal"
                  ? "bg-blue-600 text-white shadow"
                  : "text-slate-400 hover:text-slate-200"
              }`}
            >
              TERMINAL
            </button>
            <button
              onClick={() => onViewChange("strategies")}
              className={`px-3 py-1 rounded text-[11px] font-bold tracking-wider flex items-center gap-1.5 transition ${
                activeView === "strategies"
                  ? "bg-cyan-600 text-white shadow"
                  : "text-slate-400 hover:text-slate-200"
              }`}
            >
              <Activity className="w-3 h-3" />
              AUTO-STRATEGIES
            </button>
          </div>
        )}

        {/* NIFTY 50 Live Ticker */}
        <div className="hidden md:flex items-center space-x-3 px-3 py-1 bg-[#1e293b]/50 rounded border border-slate-800">
          <span className="text-slate-400 font-medium">NIFTY 50</span>
          <span className="font-mono font-semibold text-slate-100">
            {niftyQuote ? niftyQuote.last_price.toFixed(2) : "--"}
          </span>
          <span
            className={`font-mono text-[11px] font-medium ${
              (niftyQuote?.change_pct || 0) >= 0 ? "text-emerald-400" : "text-rose-400"
            }`}
          >
            {(niftyQuote?.change_pct || 0) >= 0 ? "+" : ""}
            {niftyQuote ? niftyQuote.change_pct.toFixed(2) : "+0.00"}%
          </span>
        </div>

        {/* Day P&L */}
        <div className="hidden lg:flex items-center space-x-2 px-3 py-1 bg-[#1e293b]/50 rounded border border-slate-800">
          <span className="text-slate-400">Day P&L:</span>
          <span
            className={`font-mono font-semibold ${
              (pnl?.day_pnl || 0) >= 0 ? "text-emerald-400" : "text-rose-400"
            }`}
          >
            ₹{pnl?.day_pnl ? pnl.day_pnl.toFixed(2) : "0.00"}
          </span>
          <span className="text-[11px] text-slate-500">
            ({pnl?.open_positions_count || 0} open)
          </span>
        </div>
      </div>

      {/* Global Status Badges & Controls */}
      <div className="flex items-center space-x-3">
        {/* Broker Session */}
        <button
          onClick={() => setShowAuthModal(true)}
          className={`flex items-center space-x-1.5 px-2.5 py-1 rounded font-mono border transition ${
            isBrokerActive
              ? "bg-emerald-950/40 text-emerald-400 border-emerald-800/60 hover:bg-emerald-900/30 cursor-pointer"
              : isBrokerExpired
              ? "bg-rose-950/40 text-rose-300 border-rose-800/60 hover:bg-rose-900/50 cursor-pointer animate-pulse"
              : "bg-amber-950/40 text-amber-300 border-amber-800/60 hover:bg-amber-900/50 cursor-pointer"
          }`}
          title={
            isBrokerActive
              ? `Broker sessions: ${connectedBrokerCount}/2 connected`
              : isBrokerExpired
              ? "Broker session expired. Click to reconnect Breeze or Kite."
              : "Click to connect Breeze and/or Kite"
          }
        >
          <Lock className="w-3 h-3" />
          <span>
            {isBrokerActive
              ? `BROKERS ${connectedBrokerCount}/2`
              : isBrokerExpired
              ? "SESSION EXPIRED"
              : "CONNECT BROKERS"}
          </span>
        </button>

        {/* Market Feed Status */}
        <div
          className={`flex items-center space-x-1.5 px-2 py-1 rounded font-mono border ${
            marketFeedStatus === "LIVE"
              ? "bg-teal-950/40 text-teal-400 border-teal-800/60"
              : "bg-amber-950/40 text-amber-400 border-amber-800/60"
          }`}
          title="Market Data Stream Health"
        >
          {wsConnected ? <Wifi className="w-3 h-3" /> : <WifiOff className="w-3 h-3" />}
          <span>FEED: {marketFeedStatus}</span>
        </div>

        {/* Trading Mode Switcher */}
        <div className="flex items-center bg-slate-900 border border-slate-800 rounded p-0.5">
          {(["PAPER", "SHADOW", "LIVE"] as const).map((m) => (
            <button
              key={m}
              onClick={() => setTradingMode(m)}
              className={`px-2.5 py-0.5 rounded text-[10px] font-bold tracking-wider transition ${
                tradingMode === m
                  ? m === "LIVE"
                    ? "bg-rose-600 text-white shadow"
                    : "bg-blue-600 text-white shadow"
                  : "text-slate-400 hover:text-slate-200"
              }`}
            >
              {m}
            </button>
          ))}
        </div>

        {/* Safety Mode Indicator */}
        <div
          className={`flex items-center space-x-1.5 px-2.5 py-1 rounded font-semibold tracking-wider text-[10px] border ${
            safetyMode === "NORMAL"
              ? "bg-emerald-950/30 text-emerald-400 border-emerald-800/50"
              : "bg-rose-950/40 text-rose-300 border-rose-800/80 animate-pulse"
          }`}
        >
          <ShieldAlert className="w-3.5 h-3.5" />
          <span>{safetyMode}</span>
        </div>

        {/* Emergency Kill Switch Trigger */}
        <button
          onClick={() => setKillSwitchModalOpen(true)}
          className="flex items-center space-x-1 px-2.5 py-1 bg-rose-600/20 hover:bg-rose-600/40 text-rose-300 border border-rose-500/50 rounded font-semibold transition active:scale-95"
        >
          <AlertOctagon className="w-3.5 h-3.5 text-rose-400" />
          <span>KILL SWITCH</span>
        </button>
      </div>

      {/* Configured broker connection modal */}
      {showAuthModal && (
        <div className="fixed inset-0 bg-black/70 backdrop-blur-sm z-50 flex items-center justify-center p-4">
          <div className="bg-[#0f172a] border border-slate-800 rounded-xl max-w-md w-full p-6 shadow-2xl space-y-4 text-slate-200">
            <div className="flex items-center justify-between border-b border-slate-800 pb-3">
              <div className="flex items-center space-x-2">
                <Lock className="w-4 h-4 text-blue-400" />
                <h3 className="text-sm font-bold text-slate-100 uppercase tracking-wider">
                  Broker Sessions
                </h3>
              </div>
              <button
                onClick={() => setShowAuthModal(false)}
                className="text-slate-400 hover:text-slate-200 p-1 rounded transition"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            <div className="space-y-3 text-xs leading-relaxed text-slate-400">
              <div className="grid grid-cols-2 gap-2">
                {(["kite", "breeze"] as const).map((broker) => {
                  const connected = broker === "kite" ? kiteConnected : breezeConnected;
                  return (
                    <button
                      key={broker}
                      type="button"
                      onClick={() => {
                        setAuthBroker(broker);
                        setTokenInput("");
                        setAuthError("");
                        setAuthSuccess("");
                      }}
                      className={`rounded-lg border px-3 py-2 text-xs font-bold transition ${
                        authBroker === broker
                          ? "bg-blue-600/20 text-blue-300 border-blue-500/60"
                          : "bg-slate-900 text-slate-400 border-slate-800 hover:text-slate-200"
                      }`}
                    >
                      {broker === "kite" ? "Kite" : "ICICI Breeze"} · {connected ? "CONNECTED" : "DISCONNECTED"}
                    </button>
                  );
                })}
              </div>
              <p>
                Authenticate {brokerLabel} independently. Connecting it does not disconnect the other broker or change the default execution broker.
              </p>

              {/* Step 1 */}
              <div className="bg-slate-900/80 border border-slate-800 rounded-lg p-3 space-y-2">
                <div className="font-semibold text-slate-200 flex items-center space-x-1.5">
                  <span className="w-4 h-4 rounded-full bg-blue-600 text-white flex items-center justify-center text-[10px]">1</span>
                  <span>Launch {brokerLabel} Login</span>
                </div>
                <button
                  type="button"
                  onClick={handleConnectBroker}
                  className="w-full flex items-center justify-center space-x-2 py-2 px-3 bg-blue-600 hover:bg-blue-500 text-white font-semibold rounded text-xs transition"
                >
                  <ExternalLink className="w-3.5 h-3.5" />
                  <span>Open {brokerLabel} Portal</span>
                </button>
              </div>

              {/* Step 2 */}
              <form onSubmit={handleManualActivate} className="bg-slate-900/80 border border-slate-800 rounded-lg p-3 space-y-2.5">
                <div className="font-semibold text-slate-200 flex items-center space-x-1.5">
                  <span className="w-4 h-4 rounded-full bg-blue-600 text-white flex items-center justify-center text-[10px]">2</span>
                  <span>Paste Redirect URL or {authBroker === "kite" ? "Request Token" : "Session Token"}</span>
                </div>
                <p className="text-[11px] text-slate-500">
                  If redirected to <em>&quot;This site can&apos;t be reached&quot;</em>, copy the URL from your browser address bar and paste it here:
                </p>
                <input
                  type="text"
                  value={tokenInput}
                  onChange={(e) => setTokenInput(e.target.value)}
                  placeholder={authBroker === "kite"
                    ? "e.g. https://127.0.0.1/?request_token=..."
                    : "e.g. https://127.0.0.1/?apisession=57052722"}
                  className="w-full bg-slate-950 border border-slate-700 rounded px-2.5 py-1.5 font-mono text-xs text-slate-100 placeholder-slate-600 focus:outline-none focus:border-blue-500"
                />
                {authError && (
                  <div className="text-[11px] text-rose-400 bg-rose-950/40 border border-rose-800/60 p-2 rounded">
                    {authError}
                  </div>
                )}
                {authSuccess && (
                  <div className="text-[11px] text-emerald-400 bg-emerald-950/40 border border-emerald-800/60 p-2 rounded">
                    {authSuccess}
                  </div>
                )}
                <button
                  type="submit"
                  disabled={!tokenInput.trim() || isSubmitting}
                  className="w-full py-2 px-3 bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 text-white font-semibold rounded text-xs transition flex items-center justify-center space-x-1.5"
                >
                  {isSubmitting ? <RefreshCw className="w-3.5 h-3.5 animate-spin" /> : <Lock className="w-3.5 h-3.5" />}
                  <span>{isSubmitting ? "Activating Session..." : "Activate & Save to .env"}</span>
                </button>
              </form>

              <div className="text-[10px] text-slate-500 leading-normal">
                💡 <span className="font-medium text-slate-400">Callback:</span> Configure the selected broker to return to <code className="text-blue-400">http://127.0.0.1:8000/api/v1/broker/session/callback</code>.
              </div>
            </div>
          </div>
        </div>
      )}
    </header>
  );
}
