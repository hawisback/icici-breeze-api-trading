"use client";

import React, { useEffect, useRef } from "react";
import { useQuery } from "@tanstack/react-query";
import { createChart, ColorType, IChartApi, ISeriesApi } from "lightweight-charts";
import { fetchCandles } from "@/lib/api";
import { useTradingStore } from "@/stores/useTradingStore";

export function TradingChart() {
  const chartContainerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleSeriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const volumeSeriesRef = useRef<ISeriesApi<"Histogram"> | null>(null);

  const { selectedSymbol, selectedInstrumentId, chartInterval, setChartInterval } = useTradingStore();

  const { data: candles, isLoading } = useQuery({
    queryKey: ["candles", selectedInstrumentId, chartInterval],
    queryFn: () => fetchCandles(selectedInstrumentId, chartInterval),
    refetchInterval: 5000,
  });

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

  return (
    <div className="flex flex-col h-full bg-[#0a0e17] select-none">
      {/* Chart Toolbar */}
      <div className="h-9 px-3 border-b border-[#1e293b] flex items-center justify-between text-xs">
        <div className="flex items-center space-x-3">
          <span className="font-bold text-slate-100">{selectedSymbol}</span>
          <span className="text-[10px] text-slate-500 font-mono">CANDLESTICK</span>
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

