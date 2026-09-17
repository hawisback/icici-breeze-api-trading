import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";

const WS_URL = process.env.NEXT_PUBLIC_WS_URL || "ws://localhost:8000/ws/live";

export function useTradingWebSocket() {
  const queryClient = useQueryClient();
  const [connected, setConnected] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    let ws: WebSocket;
    let reconnectTimeout: NodeJS.Timeout;

    function connect() {
      try {
        ws = new WebSocket(WS_URL);
        wsRef.current = ws;

        ws.onopen = () => {
          setConnected(true);
        };

        ws.onmessage = (event) => {
          try {
            const message = JSON.parse(event.data);
            const { type, data } = message;

            if (type === "QUOTE") {
              // Invalidate or update quote query with upsert
              queryClient.setQueryData(["quotes"], (old: any[] | undefined) => {
                if (!old || !Array.isArray(old)) return [data];
                const idx = old.findIndex(
                  (q) => q.instrument_id === data.instrument_id || q.symbol === data.symbol
                );
                if (idx >= 0) {
                  const updated = [...old];
                  updated[idx] = { ...updated[idx], ...data };
                  return updated;
                }
                return [...old, data];
              });
            } else if (type === "CANDLE") {
              queryClient.invalidateQueries({ queryKey: ["candles"] });
            } else if (type === "STRATEGY") {
              if (data?.event === "STATUS_UPDATE" && data?.data) {
                queryClient.setQueryData(["strategy_status"], data.data);
              } else if (data?.event === "DECISION_LOG" && data?.log) {
                queryClient.setQueryData(["strategy_decision_log"], (old: any[] | undefined) => {
                  if (!old || !Array.isArray(old)) return [data.log];
                  if (old.some((item) => item.id === data.log.id)) return old;
                  return [data.log, ...old.slice(0, 99)];
                });
                queryClient.invalidateQueries({ queryKey: ["strategy_decision_log"] });
              } else {
                queryClient.invalidateQueries({ queryKey: ["strategy_status"] });
                queryClient.invalidateQueries({ queryKey: ["strategy_trades"] });
              }
            } else if (type === "ORDER") {
              queryClient.invalidateQueries({ queryKey: ["orders"] });
              queryClient.invalidateQueries({ queryKey: ["strategy_trades"] });
            } else if (type === "POSITION") {
              queryClient.invalidateQueries({ queryKey: ["positions"] });
              queryClient.invalidateQueries({ queryKey: ["strategy_trades"] });
            } else if (type === "PNL") {
              queryClient.setQueryData(["pnl_summary"], data);
            } else if (type === "SYSTEM_HEALTH") {
              queryClient.setQueryData(["system_health"], data);
            }
          } catch (e) {
            console.error("Failed to parse websocket message", e);
          }
        };

        ws.onclose = () => {
          setConnected(false);
          reconnectTimeout = setTimeout(connect, 3000);
        };

        ws.onerror = () => {
          ws.close();
        };
      } catch (err) {
        reconnectTimeout = setTimeout(connect, 3000);
      }
    }

    connect();

    // Ping interval to keep connection alive
    const pingInterval = setInterval(() => {
      if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
        wsRef.current.send(JSON.stringify({ type: "PING" }));
      }
    }, 15000);

    return () => {
      clearInterval(pingInterval);
      clearTimeout(reconnectTimeout);
      if (wsRef.current) wsRef.current.close();
    };
  }, [queryClient]);

  return { connected };
}

