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
              // Invalidate or update quote query
              queryClient.setQueryData(["quotes"], (old: any[]) => {
                if (!old) return [data];
                return old.map((q) => (q.instrument_id === data.instrument_id ? data : q));
              });
            } else if (type === "ORDER") {
              queryClient.invalidateQueries({ queryKey: ["orders"] });
            } else if (type === "POSITION") {
              queryClient.invalidateQueries({ queryKey: ["positions"] });
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

