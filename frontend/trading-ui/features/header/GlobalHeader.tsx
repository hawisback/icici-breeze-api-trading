"use client";

import React from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Activity,
  AlertOctagon,
  CheckCircle2,
  ExternalLink,
  Lock,
  LogIn,
  LogOut,
  Power,
  RefreshCw,
  UserRound,
  ShieldAlert,
  Wifi,
  WifiOff,
  X,
} from "lucide-react";
import {
  AuthSession,
  activateBrokerSessionCallback,
  confirmLiveGate,
  fetchLiveGateStatus,
  fetchLoginUrl,
  fetchPnLSummary,
  fetchQuotes,
  fetchStrategyStatus,
  fetchSystemHealth,
  getStoredAuthSession,
  loginUser,
  logoutUser,
  requestLiveGateChallenge,
  revokeLiveGate,
  setLocalStrategyMode,
} from "@/lib/api";
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

  const { data: liveGate, refetch: refetchLiveGate } = useQuery({
    queryKey: ["live_gate_status"],
    queryFn: fetchLiveGateStatus,
    refetchInterval: 1500,
    refetchIntervalInBackground: true,
  });

  const { data: strategyStatus, refetch: refetchStrategyStatus } = useQuery({
    queryKey: ["strategy_status_header"],
    queryFn: fetchStrategyStatus,
    refetchInterval: 2000,
    refetchIntervalInBackground: true,
  });

  const localSingleUserMode = Boolean(
    health?.config?.local_single_user_mode ||
      liveGate?.local_single_user_mode,
  );

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

  const [operatorSession, setOperatorSession] = React.useState<AuthSession | null>(null);
  const [showOperatorLogin, setShowOperatorLogin] = React.useState(false);
  const [operatorUsername, setOperatorUsername] = React.useState("");
  const [operatorPassword, setOperatorPassword] = React.useState("");
  const [operatorAuthError, setOperatorAuthError] = React.useState("");
  const [operatorSubmitting, setOperatorSubmitting] = React.useState(false);
  const [showLiveGateModal, setShowLiveGateModal] = React.useState(false);
  const [liveChallenge, setLiveChallenge] = React.useState<{
    challenge_id: string;
    challenge_token: string;
  } | null>(null);
  const [liveConfirmInput, setLiveConfirmInput] = React.useState("");
  const [liveGateError, setLiveGateError] = React.useState("");
  const [liveGateSubmitting, setLiveGateSubmitting] = React.useState(false);
  const [nonLiveMode, setNonLiveMode] = React.useState<"PAPER" | "SHADOW">("PAPER");
  const [modeSwitching, setModeSwitching] = React.useState(false);
  const [modeError, setModeError] = React.useState("");

  React.useEffect(() => {
    setOperatorSession(getStoredAuthSession());
  }, []);

  React.useEffect(() => {
    const backendMode = strategyStatus?.config?.mode;
    if (!backendMode) return;
    if (backendMode === "SHADOW_ONLY") {
      setTradingMode("SHADOW");
      setNonLiveMode("SHADOW");
      return;
    }
    if (backendMode === "PAPER") {
      setTradingMode("PAPER");
      setNonLiveMode("PAPER");
      return;
    }
    if (backendMode === "LIVE") {
      setTradingMode("LIVE");
    }
  }, [strategyStatus?.config?.mode, setTradingMode]);

  const switchLocalExecutionMode = async (
    target: "PAPER" | "SHADOW" | "LIVE",
  ) => {
    setModeSwitching(true);
    setModeError("");
    try {
      const backendMode =
        target === "SHADOW" ? "SHADOW_ONLY" : target;
      const result = await setLocalStrategyMode(backendMode);
      const uiMode =
        result.mode === "SHADOW_ONLY" ? "SHADOW" : result.mode;
      setTradingMode(uiMode);
      if (uiMode !== "LIVE") {
        setNonLiveMode(uiMode);
      }
      await Promise.all([
        refetchStrategyStatus(),
        refetchLiveGate(),
        refetchHealth(),
      ]);
    } catch (err: any) {
      setModeError(err?.message || "Unable to switch trading mode.");
    } finally {
      setModeSwitching(false);
    }
  };

  const handleOperatorLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!operatorUsername.trim() || !operatorPassword) return;
    setOperatorSubmitting(true);
    setOperatorAuthError("");
    try {
      const session = await loginUser(
        operatorUsername.trim(),
        operatorPassword,
      );
      setOperatorSession(session);
      setOperatorPassword("");
      setShowOperatorLogin(false);
    } catch (err: any) {
      setOperatorAuthError(err?.message || "Authentication failed");
    } finally {
      setOperatorSubmitting(false);
    }
  };

  const handleOperatorLogout = async () => {
    try {
      await logoutUser();
    } finally {
      setOperatorSession(null);
      setOperatorUsername("");
      setOperatorPassword("");
    }
  };

  const beginLiveAuthorization = async () => {
    if (localSingleUserMode) {
      await switchLocalExecutionMode("LIVE");
      return;
    }
    if (!operatorSession) {
      setOperatorAuthError("Operator authentication is required before LIVE authorization.");
      setShowOperatorLogin(true);
      return;
    }
    if (!liveGate?.system_setting_enabled) {
      setLiveGateError("Server LIVE capability is disabled by configuration.");
      setShowLiveGateModal(true);
      return;
    }
    if (!liveGate.allowed_account_count) {
      setLiveGateError("No allowlisted LIVE account is configured.");
      setShowLiveGateModal(true);
      return;
    }
    setLiveGateSubmitting(true);
    setLiveGateError("");
    try {
      const challenge = await requestLiveGateChallenge(30);
      setLiveChallenge({
        challenge_id: challenge.challenge_id,
        challenge_token: challenge.challenge_token,
      });
      setLiveConfirmInput("");
      setShowLiveGateModal(true);
    } catch (err: any) {
      setLiveGateError(err?.message || "Unable to request LIVE authorization.");
      setShowLiveGateModal(true);
    } finally {
      setLiveGateSubmitting(false);
    }
  };

  const confirmLiveAuthorization = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!liveChallenge || !liveConfirmInput.trim()) return;
    setLiveGateSubmitting(true);
    setLiveGateError("");
    try {
      await confirmLiveGate(
        liveChallenge.challenge_id,
        liveConfirmInput.trim(),
      );
      await refetchLiveGate();
      setTradingMode("LIVE");
      setShowLiveGateModal(false);
      setLiveChallenge(null);
      setLiveConfirmInput("");
    } catch (err: any) {
      setLiveGateError(err?.message || "LIVE authorization failed.");
    } finally {
      setLiveGateSubmitting(false);
    }
  };

  const handleLiveRevoke = async () => {
    setLiveGateSubmitting(true);
    setLiveGateError("");
    try {
      await revokeLiveGate("Operator UI revocation");
      await refetchLiveGate();
      setTradingMode("PAPER");
      setShowLiveGateModal(false);
      setLiveChallenge(null);
      setLiveConfirmInput("");
    } catch (err: any) {
      setLiveGateError(err?.message || "Unable to revoke LIVE authorization.");
      setShowLiveGateModal(true);
    } finally {
      setLiveGateSubmitting(false);
    }
  };

  React.useEffect(() => {
    if (
      !localSingleUserMode &&
      tradingMode === "LIVE" &&
      liveGate &&
      !liveGate.live_authorized
    ) {
      setTradingMode("PAPER");
    }
  }, [liveGate, localSingleUserMode, tradingMode, setTradingMode]);

  const [showAuthModal, setShowAuthModal] = React.useState(false);
  const [tokenInput, setTokenInput] = React.useState("");
  const [isSubmitting, setIsSubmitting] = React.useState(false);
  const [authError, setAuthError] = React.useState("");
  const [authSuccess, setAuthSuccess] = React.useState("");
  const [brokerLoginState, setBrokerLoginState] = React.useState("");
  const brokerBackend = health?.config?.broker_backend === "kite" ? "kite" : "breeze";
  const brokerLabel = brokerBackend === "kite" ? "Kite" : "ICICI Breeze";

  const handleConnectBroker = async () => {
    if (!localSingleUserMode && !operatorSession) {
      setOperatorAuthError("Operator authentication is required before broker login.");
      setShowOperatorLogin(true);
      return;
    }
    try {
      const data = await fetchLoginUrl();
      setBrokerLoginState(data.callback_state || "");
      if (data.login_url) {
        window.open(
          data.login_url,
          `${brokerLabel}Login`,
          "width=600,height=750,menubar=no,toolbar=no,status=no,scrollbars=yes"
        );
      }
    } catch (err: any) {
      setAuthError(err?.message || `Failed to initiate ${brokerLabel} login.`);
    }
  };

  const handleManualActivate = async (e: React.FormEvent) => {
    e.preventDefault();
    let raw = tokenInput.trim();
    if (!raw) return;

    const tokenParam = brokerBackend === "kite" ? "request_token" : "apisession";
    if (raw.includes(`${tokenParam}=`)) {
      const match = raw.match(new RegExp(`${tokenParam}=([a-zA-Z0-9_-]+)`));
      if (match) raw = match[1];
    }

    if (!localSingleUserMode && !operatorSession) {
      setOperatorAuthError("Operator authentication is required before broker activation.");
      setShowOperatorLogin(true);
      return;
    }
    if (!brokerLoginState) {
      setAuthError("Start the broker login flow first so a secure callback state is issued.");
      return;
    }

    setIsSubmitting(true);
    setAuthError("");
    setAuthSuccess("");

    try {
      const data = await activateBrokerSessionCallback(
        tokenParam,
        raw,
        brokerLoginState,
      );
      if (data.status === "SUCCESS") {
        setAuthSuccess(`${brokerLabel} successfully authenticated and session saved to .env!`);
        await refetchHealth();
        setTimeout(() => {
          setShowAuthModal(false);
          setTokenInput("");
          setBrokerLoginState("");
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
  const isBrokerActive = brokerSessionStatus === "CONNECTED";
  const isBrokerExpired = brokerSessionStatus === "EXPIRED";
  const marketFeedStatus = health?.services?.market_feed || "LIVE";

  return (
    <header className="h-14 bg-[#0f172a] border-b border-[#1e293b] px-4 flex items-center justify-between select-none text-xs">
      {/* Brand & Market Ticker */}
      <div className="flex items-center space-x-6">
        <div className="flex items-center space-x-2">
          <div className="w-2.5 h-2.5 rounded-full bg-blue-500 animate-pulse" />
          <span className="font-bold tracking-wider text-sm bg-gradient-to-r from-blue-400 to-teal-300 bg-clip-text text-transparent">
            ICICI BREEZE TERMINAL
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
        {/* Platform Operator Authentication */}
        {localSingleUserMode ? (
          <div
            className="flex items-center gap-1.5 px-2.5 py-1 rounded font-mono border bg-cyan-950/40 text-cyan-300 border-cyan-800/60"
            title="Loopback-only single-user mode; local controls do not require operator login."
          >
            <UserRound className="w-3 h-3" />
            <span>LOCAL USER</span>
          </div>
        ) : operatorSession ? (
          <div className="flex items-center gap-1.5">
            <div
              className="flex items-center gap-1.5 px-2.5 py-1 rounded font-mono border bg-indigo-950/40 text-indigo-300 border-indigo-800/60"
              title={`Authenticated as ${operatorSession.user.username}`}
            >
              <UserRound className="w-3 h-3" />
              <span>
                {operatorSession.user.username.toUpperCase()} · {operatorSession.user.role}
              </span>
            </div>
            <button
              onClick={handleOperatorLogout}
              className="p-1.5 rounded border border-slate-700 text-slate-400 hover:text-slate-100 hover:bg-slate-800 transition"
              title="Sign out operator session"
            >
              <LogOut className="w-3.5 h-3.5" />
            </button>
          </div>
        ) : (
          <button
            onClick={() => {
              setOperatorAuthError("");
              setShowOperatorLogin(true);
            }}
            className="flex items-center gap-1.5 px-2.5 py-1 rounded font-mono border bg-amber-950/40 text-amber-300 border-amber-800/60 hover:bg-amber-900/40 transition"
            title="Authenticate before using trading or strategy controls"
          >
            <LogIn className="w-3 h-3" />
            <span>OPERATOR LOGIN</span>
          </button>
        )}

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
              ? "Broker Connected (Click to view session)"
              : isBrokerExpired
              ? `Daily ${brokerLabel} session expired. Click to authenticate today's token.`
              : `Click to Connect ${brokerLabel}`
          }
        >
          <Lock className="w-3 h-3" />
          <span>
            {isBrokerActive
              ? "BROKER CONNECTED"
              : isBrokerExpired
              ? "SESSION EXPIRED"
              : "CONNECT BROKER"}
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
        {localSingleUserMode ? (
          <div
            className="flex items-center gap-1 rounded border border-slate-800 bg-slate-900 p-0.5"
            title={
              modeError ||
              "LIVE switch changes backend mode and always leaves the strategy system disarmed."
            }
          >
            {(["PAPER", "SHADOW"] as const).map((mode) => (
              <button
                key={mode}
                type="button"
                disabled={modeSwitching}
                onClick={() => {
                  setNonLiveMode(mode);
                  if (tradingMode !== "LIVE") {
                    void switchLocalExecutionMode(mode);
                  }
                }}
                className={`px-2 py-0.5 rounded text-[9px] font-bold tracking-wider transition disabled:opacity-50 ${
                  nonLiveMode === mode
                    ? "bg-blue-600 text-white"
                    : "text-slate-500 hover:text-slate-200"
                }`}
              >
                {mode}
              </button>
            ))}
            <button
              type="button"
              role="switch"
              aria-checked={tradingMode === "LIVE"}
              disabled={modeSwitching}
              onClick={() =>
                void switchLocalExecutionMode(
                  tradingMode === "LIVE" ? nonLiveMode : "LIVE",
                )
              }
              className={`ml-1 flex items-center gap-1.5 rounded px-2 py-0.5 text-[10px] font-bold tracking-wider transition disabled:opacity-50 ${
                tradingMode === "LIVE"
                  ? "bg-rose-600 text-white"
                  : "bg-slate-800 text-slate-300"
              }`}
            >
              <span
                className={`relative inline-flex h-3.5 w-6 rounded-full transition ${
                  tradingMode === "LIVE" ? "bg-rose-300" : "bg-slate-600"
                }`}
              >
                <span
                  className={`absolute top-0.5 h-2.5 w-2.5 rounded-full bg-white transition-all ${
                    tradingMode === "LIVE" ? "left-3" : "left-0.5"
                  }`}
                />
              </span>
              LIVE
            </button>
            {modeError && (
              <span className="px-1 text-[10px] font-bold text-rose-400">!</span>
            )}
          </div>
        ) : (
          <div className="flex items-center bg-slate-900 border border-slate-800 rounded p-0.5">
            {(["PAPER", "SHADOW", "LIVE"] as const).map((m) => (
              <button
                key={m}
                onClick={() => {
                  if (m === "LIVE") {
                    if (liveGate?.live_authorized) {
                      setTradingMode("LIVE");
                    } else {
                      void beginLiveAuthorization();
                    }
                    return;
                  }
                  setTradingMode(m);
                }}
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
        )}

        {/* Server LIVE Authorization */}
        {localSingleUserMode ? (
          <div
            className={`flex items-center gap-1.5 px-2.5 py-1 rounded font-mono border text-[10px] font-bold ${
              strategyStatus?.config?.system_armed
                ? "bg-rose-950/60 text-rose-300 border-rose-700/70"
                : "bg-emerald-950/40 text-emerald-300 border-emerald-800/60"
            }`}
            title="Local single-user mode: LIVE confirmation is bypassed; mode changes remain disarmed."
          >
            <ShieldAlert className="w-3 h-3" />
            <span>
              {strategyStatus?.config?.system_armed
                ? "LOCAL · ARMED"
                : "LOCAL · DISARMED"}
            </span>
          </div>
        ) : (
          <button
            onClick={() => {
              if (liveGate?.live_authorized) {
                setShowLiveGateModal(true);
              } else {
                void beginLiveAuthorization();
              }
            }}
            className={`flex items-center gap-1.5 px-2.5 py-1 rounded font-mono border text-[10px] font-bold transition ${
              liveGate?.live_authorized
                ? "bg-rose-950/50 text-rose-300 border-rose-700/70"
                : liveGate?.system_setting_enabled
                ? "bg-amber-950/40 text-amber-300 border-amber-800/60"
                : "bg-slate-900 text-slate-500 border-slate-800"
            }`}
            title="Server-side LIVE execution authorization"
          >
            <ShieldAlert className="w-3 h-3" />
            <span>
              {liveGate?.live_authorized
                ? `LIVE AUTH · ${Math.ceil((liveGate.time_remaining_sec || 0) / 60)}m`
                : liveGate?.system_setting_enabled
                ? "LIVE LOCKED"
                : "LIVE DISABLED"}
            </span>
          </button>
        )}

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

      {/* Server LIVE authorization modal */}
      {showLiveGateModal && (
        <div className="fixed inset-0 bg-black/75 backdrop-blur-sm z-[60] flex items-center justify-center p-4">
          <form
            onSubmit={confirmLiveAuthorization}
            className="bg-[#0f172a] border border-rose-900/60 rounded-xl max-w-md w-full p-6 shadow-2xl space-y-4 text-slate-200"
          >
            <div className="flex items-center justify-between border-b border-slate-800 pb-3">
              <div className="flex items-center gap-2">
                <ShieldAlert className="w-4 h-4 text-rose-400" />
                <h3 className="text-sm font-bold uppercase tracking-wider">
                  Server LIVE Authorization
                </h3>
              </div>
              <button
                type="button"
                onClick={() => setShowLiveGateModal(false)}
                className="text-slate-400 hover:text-slate-200"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            {liveGate?.live_authorized ? (
              <>
                <div className="text-xs text-rose-200 bg-rose-950/30 border border-rose-900/50 rounded-lg p-3">
                  LIVE execution is authorized for approximately{" "}
                  <strong>{Math.ceil((liveGate.time_remaining_sec || 0) / 60)} minutes</strong>.
                  Strategy-specific live locks remain independent.
                </div>
                <button
                  type="button"
                  onClick={handleLiveRevoke}
                  disabled={liveGateSubmitting}
                  className="w-full py-2 px-3 rounded bg-rose-700 hover:bg-rose-600 disabled:opacity-50 text-white text-xs font-bold"
                >
                  Revoke LIVE Authorization Now
                </button>
              </>
            ) : liveChallenge ? (
              <>
                <div className="text-[11px] text-slate-400 leading-relaxed">
                  Type the generated confirmation token exactly to open a 30-minute
                  server authorization window. This does not remove per-strategy
                  live locks.
                </div>
                <div className="bg-slate-950 border border-slate-800 rounded p-3 font-mono text-center text-amber-300 tracking-wider">
                  {liveChallenge.challenge_token}
                </div>
                <input
                  value={liveConfirmInput}
                  onChange={(e) => setLiveConfirmInput(e.target.value)}
                  placeholder="Type confirmation token"
                  className="w-full bg-slate-950 border border-slate-700 rounded px-3 py-2 text-xs font-mono focus:outline-none focus:border-rose-500"
                />
                <button
                  type="submit"
                  disabled={
                    liveGateSubmitting ||
                    liveConfirmInput.trim() !== liveChallenge.challenge_token
                  }
                  className="w-full py-2 px-3 rounded bg-rose-700 hover:bg-rose-600 disabled:opacity-40 text-white text-xs font-bold"
                >
                  {liveGateSubmitting ? "Confirming..." : "Confirm LIVE Authorization"}
                </button>
              </>
            ) : (
              <div className="text-xs text-slate-400">
                {liveGate?.system_setting_enabled
                  ? "Request a new LIVE challenge from the LIVE selector."
                  : "LIVE_TRADING_ENABLED is false on the server. Browser controls cannot override it."}
              </div>
            )}

            {liveGateError && (
              <div className="text-[11px] text-rose-400 bg-rose-950/40 border border-rose-800/60 p-2 rounded">
                {liveGateError}
              </div>
            )}
          </form>
        </div>
      )}

      {/* Platform operator authentication modal */}
      {showOperatorLogin && (
        <div className="fixed inset-0 bg-black/70 backdrop-blur-sm z-50 flex items-center justify-center p-4">
          <form
            onSubmit={handleOperatorLogin}
            className="bg-[#0f172a] border border-slate-800 rounded-xl max-w-sm w-full p-6 shadow-2xl space-y-4 text-slate-200"
          >
            <div className="flex items-center justify-between border-b border-slate-800 pb-3">
              <div className="flex items-center gap-2">
                <UserRound className="w-4 h-4 text-indigo-400" />
                <h3 className="text-sm font-bold uppercase tracking-wider">
                  Platform Operator Login
                </h3>
              </div>
              <button
                type="button"
                onClick={() => setShowOperatorLogin(false)}
                className="text-slate-400 hover:text-slate-200"
              >
                <X className="w-4 h-4" />
              </button>
            </div>
            <p className="text-[11px] leading-relaxed text-slate-500">
              Required for trading, strategy controls, safety-mode changes,
              and manual exits. Broker login remains a separate daily session.
            </p>
            <input
              autoComplete="username"
              value={operatorUsername}
              onChange={(e) => setOperatorUsername(e.target.value)}
              placeholder="Username"
              className="w-full bg-slate-950 border border-slate-700 rounded px-3 py-2 text-xs focus:outline-none focus:border-indigo-500"
            />
            <input
              type="password"
              autoComplete="current-password"
              value={operatorPassword}
              onChange={(e) => setOperatorPassword(e.target.value)}
              placeholder="Password"
              className="w-full bg-slate-950 border border-slate-700 rounded px-3 py-2 text-xs focus:outline-none focus:border-indigo-500"
            />
            {operatorAuthError && (
              <div className="text-[11px] text-rose-400 bg-rose-950/40 border border-rose-800/60 p-2 rounded">
                {operatorAuthError}
              </div>
            )}
            <button
              type="submit"
              disabled={
                !operatorUsername.trim() ||
                !operatorPassword ||
                operatorSubmitting
              }
              className="w-full py-2 px-3 rounded bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 text-white text-xs font-semibold flex items-center justify-center gap-2"
            >
              {operatorSubmitting ? (
                <RefreshCw className="w-3.5 h-3.5 animate-spin" />
              ) : (
                <LogIn className="w-3.5 h-3.5" />
              )}
              <span>{operatorSubmitting ? "Signing in..." : "Sign In"}</span>
            </button>
          </form>
        </div>
      )}

      {/* Configured broker connection modal */}
      {showAuthModal && (
        <div className="fixed inset-0 bg-black/70 backdrop-blur-sm z-50 flex items-center justify-center p-4">
          <div className="bg-[#0f172a] border border-slate-800 rounded-xl max-w-md w-full p-6 shadow-2xl space-y-4 text-slate-200">
            <div className="flex items-center justify-between border-b border-slate-800 pb-3">
              <div className="flex items-center space-x-2">
                <Lock className="w-4 h-4 text-blue-400" />
                <h3 className="text-sm font-bold text-slate-100 uppercase tracking-wider">
                  Connect {brokerLabel} Session
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
              <p>
                To authenticate your daily broker session, log in on the official {brokerLabel} portal.
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
                  <span>Paste Redirect URL or {brokerBackend === "kite" ? "Request Token" : "Session Token"}</span>
                </div>
                <p className="text-[11px] text-slate-500">
                  If redirected to <em>&quot;This site can&apos;t be reached&quot;</em>, copy the URL from your browser address bar and paste it here:
                </p>
                <input
                  type="text"
                  value={tokenInput}
                  onChange={(e) => setTokenInput(e.target.value)}
                  placeholder={brokerBackend === "kite"
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
                💡 <span className="font-medium text-slate-400">Zero-copy auto-redirect:</span> In your ICICI Direct Developer Console, set your Redirect URL to <code className="text-blue-400">http://127.0.0.1:8000/api/v1/broker/session/callback</code>.
              </div>
            </div>
          </div>
        </div>
      )}
    </header>
  );
}
