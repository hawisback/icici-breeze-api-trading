"use client";

import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

const API = `${process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api/v1"}/ai`;
type Broker = "kite" | "breeze";

type Session = {
  broker: Broker;
  configured: boolean;
  connected: boolean;
  status: string;
  expires_at: string | null;
  message?: string | null;
};

type Sessions = {
  kite: Session;
  breeze: Session;
  market_data_broker: string;
  trading_mode: string;
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API}${path}`, {
    cache: "no-store",
    ...init,
  });
  const data: unknown = await response.json().catch(() => ({}));
  if (!response.ok) {
    const payload = data as { detail?: string; message?: string };
    throw new Error(payload.detail || payload.message || `HTTP ${response.status}`);
  }
  return data as T;
}

function istTime(iso: string | null | undefined): string | null {
  if (!iso) return null;
  return new Date(iso).toLocaleString("en-IN", {
    timeZone: "Asia/Kolkata",
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/**
 * Broker authentication is the ONLY action in this read-only trading UI.
 * It does not create an order, toggle execution mode, or start data polling.
 */
export default function BrokerConnections() {
  const queryClient = useQueryClient();
  const [connecting, setConnecting] = useState<Broker | null>(null);
  const [submitting, setSubmitting] = useState<Broker | null>(null);
  const [manual, setManual] = useState<Broker | null>(null);
  const [token, setToken] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [fallbackLogin, setFallbackLogin] = useState<string | null>(null);

  const sessions = useQuery({
    queryKey: ["ai-broker-sessions"],
    queryFn: () => request<Sessions>("/broker/sessions"),
    refetchInterval: connecting ? 5000 : 30000,
  });

  useEffect(() => {
    if (connecting && sessions.data?.[connecting]?.connected) {
      setConnecting(null);
      setNotice(`${connecting === "kite" ? "Kite" : "Breeze"} connected successfully.`);
    }
  }, [connecting, sessions.data]);

  async function openBrokerLogin(broker: Broker) {
    setError(null);
    setNotice(null);
    setFallbackLogin(null);
    // Open synchronously in the click handler to avoid browser popup blocking
    // after waiting on an HTTP request.
    const popup = window.open("about:blank", "_blank");
    setConnecting(broker);
    try {
      const response = await request<{ login_url: string }>(
        "/broker/session/login-url",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ broker }),
        },
      );
      setFallbackLogin(response.login_url);
      if (popup) {
        popup.location.href = response.login_url;
      } else {
        setNotice("Popup blocked. Use the 'Open broker login link' below.");
      }
    } catch (cause) {
      if (popup) popup.close();
      setConnecting(null);
      setError(cause instanceof Error ? cause.message : "Unable to start broker login.");
    }
  }

  async function activateWithToken(broker: Broker) {
    if (!token.trim()) {
      setError("Enter today's broker token first.");
      return;
    }
    setSubmitting(broker);
    setError(null);
    setNotice(null);
    try {
      const response = await request<{ connected: boolean; persisted_for_restart: boolean }>(
        "/broker/session/activate",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ broker, token: token.trim() }),
        },
      );
      // Never store Kite/Breeze tokens in the browser's persistent storage.
      setToken("");
      setManual(null);
      setConnecting(null);
      setFallbackLogin(null);
      setNotice(response.persisted_for_restart
        ? "Broker connected; today's token saved in the backend .env."
        : "Broker connected, but token could not be persisted to .env.");
      await queryClient.invalidateQueries({ queryKey: ["ai-broker-sessions"] });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Session activation failed.");
    } finally {
      setSubmitting(null);
    }
  }

  return (
    <section className="space-y-3" aria-label="Daily broker connections">
      <div className="flex flex-wrap justify-between items-center gap-2">
        <div>
          <h2 className="text-lg font-semibold">Broker Connections</h2>
          <p className="text-xs text-slate-400 mt-1">
            Connect Kite for AI market data. Breeze login is optional. Reconnect when daily broker sessions expire.
          </p>
        </div>
        <button
          type="button"
          className="text-xs text-sky-300 hover:text-sky-200 border border-slate-700 px-3 py-2 rounded"
          onClick={() => void sessions.refetch()}
          disabled={sessions.isFetching}
        >Refresh status</button>
      </div>
      <div className="grid md:grid-cols-2 gap-3">
        {(["kite", "breeze"] as const).map((broker) => {
          const connection = sessions.data?.[broker];
          const ready = Boolean(connection?.configured);
          const connected = Boolean(connection?.connected);
          const label = broker === "kite" ? "Zerodha Kite" : "ICICI Breeze";
          return (
            <div key={broker} className="rounded-lg border border-slate-700 bg-[#101827] p-4 space-y-3">
              <div className="flex justify-between items-start gap-2">
                <div>
                  <h3 className="font-semibold">{label}</h3>
                  <p className="text-xs text-slate-500 mt-1">
                    {broker === "kite" ? "Primary options & market-data feed" : "Optional broker account connection"}
                  </p>
                </div>
                <span className={`rounded px-2 py-1 text-xs border ${connected
                  ? "border-emerald-800 bg-emerald-950/40 text-emerald-300"
                  : "border-amber-900 bg-amber-950/30 text-amber-300"}`}>
                  {connected ? "CONNECTED" : !ready && sessions.data ? "NOT CONFIGURED" : (connection?.status || "DISCONNECTED")}
                </span>
              </div>
              <p className="text-xs text-slate-400">
                {connected ? `Session expiry: ${istTime(connection?.expires_at) || "not reported"}`
                  : ready ? "Daily broker authentication required."
                    : connection ? `Backend credentials are missing. Set ${broker === "kite" ? "KITE_API_KEY and KITE_API_SECRET" : "BREEZE_API_KEY and BREEZE_SECRET_KEY"} in the project's .env and restart Uvicorn, then refresh status.`
                    : "Checking the local broker API… You can still attempt to connect."}
              </p>
              <div className="flex flex-wrap items-center gap-2">
                <button
                  type="button"
                  className="rounded bg-blue-600 hover:bg-blue-500 disabled:opacity-50 px-3 py-2 text-xs font-medium"
                  disabled={connecting === broker || submitting !== null}
                  onClick={() => void openBrokerLogin(broker)}
                >
                  {connecting === broker ? "Waiting for login…" : connected ? "Reconnect" : `Connect ${broker === "kite" ? "Kite" : "Breeze"}`}
                </button>
                <button
                  type="button"
                  className="rounded border border-slate-600 hover:border-slate-400 px-3 py-2 text-xs"
                  onClick={() => {
                    setManual(manual === broker ? null : broker);
                    setToken("");
                    setError(null);
                  }}
                >{manual === broker ? "Hide token input" : "Enter token manually"}</button>
              </div>
              {manual === broker && (
                <form
                  className="space-y-2"
                  onSubmit={(event) => { event.preventDefault(); void activateWithToken(broker); }}
                >
                  <label className="block text-xs text-slate-300" htmlFor={`broker-token-${broker}`}>
                    {broker === "kite" ? "Today's Kite request_token" : "Today's Breeze apisession"}
                  </label>
                  <input
                    id={`broker-token-${broker}`}
                    type="password"
                    autoComplete="off"
                    spellCheck={false}
                    value={token}
                    onChange={(event) => setToken(event.target.value)}
                    placeholder="Paste broker token here"
                    className="w-full rounded bg-slate-950 border border-slate-700 px-3 py-2 text-sm"
                  />
                  <button
                    disabled={submitting !== null}
                    className="rounded border border-sky-700 bg-sky-950 text-sky-300 px-3 py-2 text-xs disabled:opacity-50"
                    type="submit"
                  >{submitting === broker ? "Connecting…" : "Activate session"}</button>
                </form>
              )}
            </div>
          );
        })}
      </div>
      {fallbackLogin && connecting && (
        <p className="text-xs text-sky-300">
          <a href={fallbackLogin} target="_blank" rel="noopener noreferrer" className="underline">
            Open broker login link
          </a>
          {" "}if your browser blocked the popup.
        </p>
      )}
      {sessions.error && <p role="alert" className="text-sm text-amber-300">
        Could not load broker status: {sessions.error instanceof Error ? sessions.error.message : "Unavailable"}. Check that Uvicorn is running on http://127.0.0.1:8000, then click Refresh status.
      </p>}
      {error && <p role="alert" className="text-sm text-rose-300">{error}</p>}
      {notice && <p role="status" className="text-sm text-sky-300">{notice}</p>}
      <p className="text-xs text-slate-500">
        These buttons only authenticate broker sessions. They do not submit orders, enable LIVE trading, or start the legacy strategy engine.
      </p>
    </section>
  );
}
