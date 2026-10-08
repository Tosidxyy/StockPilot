"use client";

import { useEffect, useMemo, useRef } from "react";
import type { EChartsOption, EChartsType } from "echarts";
import type { IntradayPoint, KlineItem } from "../lib/types";

const axisColor = "#697586";
const gridColor = "#222a35";

function ChartFrame({ option, label, className = "" }: { option: EChartsOption; label: string; className?: string }) {
  const element = useRef<HTMLDivElement>(null);
  const chart = useRef<EChartsType | null>(null);
  const latestOption = useRef(option);

  useEffect(() => {
    let disposed = false;
    let observer: ResizeObserver | null = null;
    void import("echarts").then((echarts) => {
      if (disposed || !element.current) return;
      const instance = echarts.init(element.current, undefined, { renderer: "canvas" });
      chart.current = instance;
      instance.setOption(latestOption.current, true);
      observer = new ResizeObserver(() => instance.resize());
      observer.observe(element.current);
    });
    return () => {
      disposed = true;
      observer?.disconnect();
      chart.current?.dispose();
      chart.current = null;
    };
  }, []);

  useEffect(() => {
    latestOption.current = option;
    chart.current?.setOption(option, true);
  }, [option]);

  return <div ref={element} className={`chart ${className}`} role="img" aria-label={label} />;
}

export function MarketLineChart({ points, name }: { points: IntradayPoint[]; name: string }) {
  const option = useMemo<EChartsOption>(() => ({
    animation: false,
    grid: { left: 52, right: 16, top: 18, bottom: 30 },
    tooltip: { trigger: "axis", backgroundColor: "#151a22", borderColor: "#2c3542", textStyle: { color: "#edf2f7" } },
    xAxis: {
      type: "category", boundaryGap: false, data: points.map((point) => point.time.slice(11, 16)),
      axisLine: { lineStyle: { color: gridColor } }, axisTick: { show: false },
      axisLabel: { color: axisColor, interval: Math.max(0, Math.floor(points.length / 5) - 1) },
    },
    yAxis: {
      type: "value", scale: true, splitLine: { lineStyle: { color: gridColor } },
      axisLabel: { color: axisColor }, axisLine: { show: false },
    },
    series: [{
      name: "指数点位", type: "line", smooth: false, showSymbol: false,
      data: points.map((point) => point.price),
      lineStyle: { color: "#6ea8fe", width: 2 },
      itemStyle: { color: "#6ea8fe" },
      areaStyle: { color: "rgba(110,168,254,.15)" },
    }],
  }), [points]);

  return <ChartFrame option={option} label={`${name}分时走势图`} />;
}

export function SentimentPieChart({ counts }: { counts: Record<"positive" | "negative" | "neutral", number> }) {
  const total = counts.positive + counts.negative + counts.neutral;
  const option = useMemo<EChartsOption>(() => ({
    animation: false,
    tooltip: { trigger: "item", backgroundColor: "#151a22", borderColor: gridColor,
      textStyle: { color: "#edf2f7" }, formatter: "{b}：{c} 条（{d}%）" },
    series: [{ type: "pie", radius: "76%", center: ["50%", "50%"],
      label: { show: false }, labelLine: { show: false },
      data: [
        { name: "积极", value: counts.positive, itemStyle: { color: "#ff596d" } },
        { name: "消极", value: counts.negative, itemStyle: { color: "#38d6a0" } },
        { name: "中立", value: counts.neutral, itemStyle: { color: "#929ca9" } },
      ],
    }],
  }), [counts]);
  return <ChartFrame option={option} className="sentiment-pie"
    label={`股民情绪占比饼图，积极${counts.positive}条，消极${counts.negative}条，中立${counts.neutral}条，共${total}条`} />;
}

export function KlineChart({ items }: { items: KlineItem[] }) {
  const option = useMemo<EChartsOption>(() => ({
    animation: false,
    tooltip: { trigger: "axis", axisPointer: { type: "cross" }, backgroundColor: "#151a22", borderColor: "#2c3542", textStyle: { color: "#edf2f7" } },
    axisPointer: { link: [{ xAxisIndex: "all" }] },
    grid: [
      { left: 54, right: 20, top: 20, height: "58%" },
      { left: 54, right: 20, top: "76%", height: "13%" },
    ],
    xAxis: [
      { type: "category", data: items.map((item) => item.date), gridIndex: 0, boundaryGap: true, axisLabel: { show: false }, axisLine: { lineStyle: { color: gridColor } }, axisTick: { show: false } },
      { type: "category", data: items.map((item) => item.date), gridIndex: 1, boundaryGap: true, axisLabel: { color: axisColor }, axisLine: { lineStyle: { color: gridColor } }, axisTick: { show: false } },
    ],
    yAxis: [
      { scale: true, gridIndex: 0, splitLine: { lineStyle: { color: gridColor } }, axisLabel: { color: axisColor } },
      { scale: true, gridIndex: 1, splitLine: { show: false }, axisLabel: { color: axisColor, formatter: (value: number) => `${Math.round(value / 10000)}万` } },
    ],
    dataZoom: [{ type: "inside", xAxisIndex: [0, 1], start: 45, end: 100 }],
    series: [
      {
        name: "K 线", type: "candlestick",
        data: items.map((item) => [item.open, item.close, item.low, item.high]),
        itemStyle: { color: "#f05252", color0: "#22c55e", borderColor: "#f05252", borderColor0: "#22c55e" },
      },
      {
        name: "成交量", type: "bar", xAxisIndex: 1, yAxisIndex: 1,
        data: items.map((item) => ({ value: item.volume, itemStyle: { color: item.close >= item.open ? "#f0525280" : "#22c55e80" } })),
      },
    ],
  }), [items]);

  return <ChartFrame option={option} label="个股 K 线与成交量图" className="kline-chart" />;
}

export function StockIntradayChart({ points, name }: { points: IntradayPoint[]; name: string }) {
  const option = useMemo<EChartsOption>(() => ({
    animation: false,
    tooltip: { trigger: "axis", axisPointer: { type: "cross" }, backgroundColor: "#151a22", borderColor: gridColor, textStyle: { color: "#edf2f7" } },
    axisPointer: { link: [{ xAxisIndex: "all" }] },
    grid: [{ left: 60, right: 20, top: 20, height: "58%" }, { left: 60, right: 20, top: "76%", height: "13%" }],
    xAxis: [0, 1].map((index) => ({
      type: "category" as const, gridIndex: index, boundaryGap: false,
      data: points.map((point) => point.time.slice(11, 16)),
      axisLabel: { show: index === 1, color: axisColor, interval: Math.max(0, Math.floor(points.length / 6) - 1) },
      axisLine: { lineStyle: { color: gridColor } }, axisTick: { show: false },
    })),
    yAxis: [
      { type: "value", scale: true, splitLine: { lineStyle: { color: gridColor } }, axisLabel: { color: axisColor } },
      { type: "value", gridIndex: 1, splitNumber: 2, splitLine: { show: false }, axisLabel: { color: axisColor, hideOverlap: true, formatter: (value: number) => value >= 10000 ? `${(value / 10000).toFixed(1)}万` : String(Math.round(value)) } },
    ],
    series: [
      { name: "价格", type: "line", showSymbol: false, data: points.map((point) => point.price), lineStyle: { color: "#6ea8fe", width: 2 }, itemStyle: { color: "#6ea8fe" }, areaStyle: { color: "rgba(110,168,254,.12)" } },
      { name: "成交量（手）", type: "bar", xAxisIndex: 1, yAxisIndex: 1, data: points.map((point, index) => ({ value: point.volume, itemStyle: { color: index > 0 && point.price < points[index - 1].price ? "#22c55e80" : "#f0525280" } })) },
    ],
  }), [points]);
  return <ChartFrame option={option} label={`${name}实时分时与成交量图`} className="kline-chart" />;
}
