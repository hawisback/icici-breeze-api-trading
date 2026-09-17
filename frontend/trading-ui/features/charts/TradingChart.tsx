"use client";

import React, { useEffect, useRef, useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { createChart, ColorType, IChartApi, ISeriesApi } from "lightweight-charts";
import { fetchCandles, fetchQuotes } from "@/lib/api";
import { useTradingStore } from "@/stores/useTradingStore";

function formatIST(isoOrUnix: string | number | undefined): string {
  if (!isoOrUnix) return "--";
  const date = typeof isoOrUnix === "number" ? new Date(isoOrUnix * 1000) : new Date(isoOrUnix);
  return date.toLocaleString("en-IN", {
    timeZone: "Asia/Kolkata",
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }) + " IST";
}

function getMarketSessionInfo() {
  const now = new Date();
  const istString = now.toLocaleString("en-US", { timeZone: "Asia/Kolkata" });
  const istDate = new Date(istString);
  const day = istDate.getDay();
  const hours = istDate.getHours();
  const minutes = istDate.getMinutes();
  const timeInMinutes = hours * 60 + minutes;

  const isWeekday = day >= 1 && day <= 5;
  const isMarketHours = timeInMinutes >= 9 * 60 + 15 && timeInMinutes <= 15 * 60 + 30;
  const isOpen = isWeekday && isMarketHours;

  return {
    isOpen,
    statusText: isOpen ? "MARKET OPEN (09:15-15:30 IST)" : "MARKET CLOSED",
  };
}

export function TradingChart() {
  const chartContainerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleSeriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const volumeSeriesRef = useRef<ISeriesApi<"Histogram"> | null>(null);

  const { selectedSymbol, selectedInstrumentId, chartInterval, setChartInterval } = useTradingStore();

  const { data: candles, isLoading } = useQuery({
    queryKey: ["candles", selectedInstrumentId, chartInterval],
    queryFn: () => fetchCandles(selectedInstrumentId, chartInterval),
    refetchInterval: 3000,
  });

  const { data: quotes } = useQuery({
    queryKey: ["quotes"],
    queryFn: fetchQuotes,
    refetchInterval: 1000,
  });

  const lastCandle = useMemo(() => {
    return candles && candles.length > 0 ? candles[candles.length - 1] : null;
  }, [candles]);

  const sessionInfo = useMemo(() => getMarketSessionInfo(), []);
  const isBreeze = lastCandle?.source === "BREEZE";
  const formattedLastTime = lastCandle ? formatIST(lastCandle.isoTime || lastCandle.time) : "--";

  useEffect(() => {
    if (!chartContainerRef.current) return;

    const chart = createChart(chartContainerRef.current, {
      layout: {
        background: { type: ColorType.Solid, color: "#0a0e17" },
        textColor: "#94a3b8",
        fontSize: 11,
      },
      grid: {
        vertLines: { color: "#1e293b" },
        horzLines: { color: "#1e293b" },
      },
      crosshair: {
        mode: 1, // CrosshairMode.Normal
      },
      rightPriceScale: {
        borderColor: "#1e293b",
      },
      timeScale: {
        borderColor: "#1e293b",
        timeVisible: true,
        secondsVisible: false,
      },
    });

    const candleSeries = chart.addCandlestickSeries({
      upColor: "#10b981",
      downColor: "#ef4444",
      borderVisible: false,
      wickUpColor: "#10b981",
      wickDownColor: "#ef4444",
    });

    const volumeSeries = chart.addHistogramSeries({
      color: "#3b82f6",
      priceFormat: {
        type: "volume",
      },
      priceScaleId: "", // overlay
    });

    volumeSeries.priceScale().applyOptions({
      scaleMargins: {
        top: 0.8,
        bottom: 0,
      },
    });

    chartRef.current = chart;
    candleSeriesRef.current = candleSeries;
    volumeSeriesRef.current = volumeSeries;

    const handleResize = () => {
      if (chartContainerRef.current && chartRef.current) {
        chartRef.current.applyOptions({
          width: chartContainerRef.current.clientWidth,
          height: chartContainerRef.current.clientHeight,
        });
      }
    };

    window.addEventListener("resize", handleResize);
    handleResize();

    return () => {
      window.removeEventListener("resize", handleResize);
      chart.remove();
    };
  }, []);

  // Update data whenever candles change
  useEffect(() => {
    if (!candles || candles.length === 0) return;

    if (candleSeriesRef.current && volumeSeriesRef.current) {
      candleSeriesRef.current.setData(candles as any);

      const volumeData = candles.map((c) => ({
        time: c.time as any,
        value: c.volume,
        color: c.close >= c.open ? "rgba(16, 185, 129, 0.4)" : "rgba(239, 68, 68, 0.4)",
      }));
      volumeSeriesRef.current.setData(volumeData);
    }
  }, [candles]);

  // Live real-time tick updates on the forming bar
  useEffect(() => {
    if (!quotes || !candleSeriesRef.current || !candles || candles.length === 0) return;
    const currentQuote = quotes.find(
      (q) => q.instrument_id === selectedInstrumentId || q.symbol === selectedSymbol
    );
    if (!currentQuote || !currentQuote.last_price) return;

    const latest = candles[candles.length - 1];
    candleSeriesRef.current.update({
      time: latest.time as any,
      open: latest.open,
      high: Math.max(latest.high, currentQuote.last_price),
      low: Math.min(latest.low, currentQuote.last_price),
      close: currentQuote.last_price,
    });
  }, [quotes, selectedInstrumentId, selectedSymbol, candles]);

  return (
    <div className="flex flex-col h-full bg-[#0a0e17] select-none">
      {/* Chart Toolbar */}
      <div className="h-9 px-3 border-b border-[#1e293b] flex items-center justify-between text-xs">
        <div className="flex items-center space-x-2">
          <span className="font-bold text-slate-100">{selectedSymbol}</span>

          {/* Data Source Badge */}
          {isBreeze ? (
            <span
              className={`px-1.5 py-0.5 rounded font-mono text-[9px] font-bold border ${
                sessionInfo.isOpen
                  ? "bg-emerald-950/80 border-emerald-600 text-emerald-300 animate-pulse"
                  : "bg-cyan-950/80 border-cyan-700 text-cyan-300"
              }`}
            >
              {sessionInfo.isOpen ? "BREEZE LIVE" : "BREEZE (LAST CLOSE)"}
            </span>
          ) : (
            <span className="px-1.5 py-0.5 rounded font-mono text-[9px] font-bold bg-amber-950/80 border border-amber-700 text-amber-300">
              SIMULATED
            </span>
          )}

          {/* Market Session Badge */}
          <span
            className={`px-1.5 py-0.5 rounded font-mono text-[9px] border hidden sm:inline ${
              sessionInfo.isOpen ? "border-emerald-800 text-emerald-400 bg-emerald-950/30" : "border-slate-800 text-slate-400 bg-slate-900/50"
            }`}
          >
            {sessionInfo.statusText}
          </span>

          {/* Last Candle Timestamp */}
          {lastCandle && (
            <span className="hidden lg:inline text-[10px] text-slate-400 font-mono">
              Bar: <span className="text-slate-200 font-semibold">{formattedLastTime}</span> (₹{lastCandle.close.toFixed(2)})
            </span>
          )}
        </div>

        {/* Interval Selector */}
        <div className="flex items-center space-x-1 bg-slate-900 border border-slate-800 rounded p-0.5">
          {["1m", "5m", "15m", "1D"].map((int) => (
            <button
              key={int}
              onClick={() => setChartInterval(int)}
              className={`px-2 py-0.5 rounded text-[10px] font-mono transition ${
                chartInterval === int
                  ? "bg-blue-600 text-white font-bold"
                  : "text-slate-400 hover:text-slate-200"
              }`}
            >
              {int}
            </button>
          ))}
        </div>
      </div>

      {/* Chart Canvas */}
      <div className="flex-1 relative w-full h-full min-h-[300px]">
        {isLoading && (
          <div className="absolute inset-0 z-10 flex items-center justify-center bg-[#0a0e17]/80 text-slate-500 font-mono text-xs">
            Loading chart data...
          </div>
        )}
        <div ref={chartContainerRef} className="w-full h-full" />
      </div>
    </div>
  );
}
