"use client";

import {
  CandlestickSeries,
  createChart,
  type CandlestickData,
  type ColorType,
  type IChartApi,
  type ISeriesApi,
} from "lightweight-charts";
import { useEffect, useRef } from "react";
import type { Candle, LatestSignal } from "@/lib/types";

function priceColor(kind: "entry" | "stop" | "target") {
  if (kind === "entry") {
    return "#0ea5e9";
  }
  if (kind === "stop") {
    return "#e11d48";
  }
  return "#059669";
}

export function KlineChart({
  candles,
  symbol,
  signal,
}: {
  candles: Candle[];
  symbol: string;
  signal: LatestSignal | null;
}) {
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!ref.current) {
      return;
    }

    const chart: IChartApi = createChart(ref.current, {
      height: 430,
      layout: {
        background: { type: "solid" as ColorType.Solid, color: "#101616" },
        textColor: "#d4d4d8",
      },
      grid: {
        vertLines: { color: "#1d3332" },
        horzLines: { color: "#1d3332" },
      },
      timeScale: {
        borderColor: "#3f3f46",
        timeVisible: true,
        secondsVisible: false,
      },
      rightPriceScale: {
        borderColor: "#3f3f46",
      },
      crosshair: {
        mode: 1,
      },
    });

    const series: ISeriesApi<"Candlestick"> = chart.addSeries(CandlestickSeries, {
      upColor: "#0abab5",
      downColor: "#f43f5e",
      borderVisible: false,
      wickUpColor: "#0abab5",
      wickDownColor: "#f43f5e",
    });

    series.setData(candles as CandlestickData[]);
    chart.timeScale().fitContent();

    const currentSignal = signal?.signal;
    if (currentSignal?.inst_id === symbol) {
      const lines = [
        ["entry", currentSignal.entry_ref, "入场"] as const,
        ["stop", currentSignal.stop_loss, "止损"] as const,
        ["target", currentSignal.take_profit, "止盈"] as const,
      ];
      lines.forEach(([kind, price, title]) => {
        if (typeof price === "number") {
          series.createPriceLine({
            price,
            color: priceColor(kind),
            lineWidth: 2,
            lineStyle: 2,
            axisLabelVisible: true,
            title,
          });
        }
      });
    }

    const resize = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (entry) {
        chart.applyOptions({ width: Math.floor(entry.contentRect.width) });
      }
    });
    resize.observe(ref.current);

    return () => {
      resize.disconnect();
      chart.remove();
    };
  }, [candles, signal, symbol]);

  return <div ref={ref} className="h-[430px] w-full overflow-hidden rounded-lg" />;
}
