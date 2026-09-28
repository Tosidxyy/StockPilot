"use client";

import { useState } from "react";
import { amount, cachedTime, moveClass, number, signed, updatedTime } from "../lib/format";
import type { IntradayPoint, MarketIndex } from "../lib/types";
import { useResource } from "../lib/use-resource";
import { MarketLineChart } from "./Charts";
import { AgentChat } from "./AgentChat";
import { TracePanel } from "./TracePanel";
import { WatchlistPanel } from "./WatchlistPanel";
import { NewsPanel } from "./NewsPanel";

const indexNames: Record<string, string> = {
  "000001": "上证指数", "399001": "深证成指", "399006": "创业板指",
};

export function MarketDashboard() {
  const [selected, setSelected] = useState("000001");
  const [traceVersion, setTraceVersion] = useState(0);
  const indices = useResource<MarketIndex[]>("/api/market/indices", 2000);
  const trend = useResource<IntradayPoint[]>(`/api/market/indices/${selected}/intraday`, 2000);
  const active = indices.data?.find((item) => item.symbol === selected);

  return (
    <main className="content">
      <div className="hero"><div><p className="eyebrow">STOCKPILOT / OVERVIEW</p><h1>市场概览</h1><p>指数走势、自选股和研究入口集中在一个工作台。</p></div><span className="chip">实时接口 · P0</span></div>

      {indices.loading ? <div className="indices"><div className="card skeleton index-card" /><div className="card skeleton index-card" /><div className="card skeleton index-card" /></div> :
        indices.error && !indices.data ? <div className="card section-state error-state">{indices.error}<button onClick={indices.refresh}>重试</button></div> :
          !indices.data?.length ? <div className="card section-state">暂无指数数据。</div> :
            <section className="indices" aria-label="三大指数">{indices.data.map((index) => (
              <button key={index.symbol} type="button" className={`card index-card ${selected === index.symbol ? "selected" : ""}`} onClick={() => setSelected(index.symbol)} aria-pressed={selected === index.symbol}>
                <span className="kicker">{index.name}</span>
                <span className="index-main"><strong>{number(index.value)}</strong><em className={moveClass(index.change_percent)}>{signed(index.change_percent)}</em></span>
                <span className="index-sub"><span className={moveClass(index.change_amount)}>{signed(index.change_amount, "")}</span><span>成交额 {amount(index.turnover)}</span></span>
              </button>
            ))}</section>}
      {indices.stale && <p className="stale-note">指数暂未更新，正在显示旧缓存{cachedTime(indices.cachedAt)}。<button className="text-button" onClick={indices.refresh}>重试更新</button></p>}
      {!indices.stale && indices.cachedAt && <p className="card-note">指数每 2 秒刷新{updatedTime(indices.cachedAt)}</p>}

      <div className="dashboard-grid">
        <div className="left-col">
          <section className="card market-card" aria-labelledby="trend-title">
            <div className="section-head"><div><h2 id="trend-title">{indexNames[selected]} · 分时走势</h2><span>{trend.data?.length ? trend.data[trend.data.length - 1].time.slice(0, 10) : "最新交易日"} · 每 2 秒刷新</span></div><span className="soft-label">09:30 — 15:00</span></div>
            <div className="market-meta">
              <div className="metric"><span>最新</span><strong>{number(active?.value)}</strong></div>
              <div className="metric"><span>涨跌幅</span><strong className={moveClass(active?.change_percent)}>{signed(active?.change_percent)}</strong></div>
              <div className="metric"><span>最高</span><strong>{number(active?.high)}</strong></div>
              <div className="metric"><span>最低</span><strong>{number(active?.low)}</strong></div>
              <div className="metric"><span>成交额</span><strong>{amount(active?.turnover)}</strong></div>
            </div>
            <div className="chart-wrap">
              {trend.loading ? <div className="chart skeleton" /> :
                trend.error && !trend.data ? <div className="section-state error-state">{trend.error}<button onClick={trend.refresh}>重试</button></div> :
                  !trend.data?.length ? <div className="section-state">最新交易日暂无分时数据。</div> :
                    <MarketLineChart points={trend.data} name={indexNames[selected]} />}
            </div>
            {trend.stale && <p className="stale-note">分时暂未更新，正在显示旧缓存{cachedTime(trend.cachedAt)}。<button className="text-button" onClick={trend.refresh}>重试更新</button></p>}
          </section>

          <WatchlistPanel />
          <NewsPanel />

          <section className="card temperature-card" aria-labelledby="temperature-title">
            <div className="section-head"><div><h2 id="temperature-title">市场温度</h2><span>全市场 · P1 数据待接入</span></div></div>
            <div className="overview-grid">
              {["上涨", "下跌", "涨停", "两市成交额"].map((label) => <div className="mini" key={label}><span>{label}</span><strong>—</strong></div>)}
            </div>
            <p className="card-note">当前数据接口不提供全市场涨跌家数；接入后在此显示真实统计。</p>
          </section>
        </div>

        <aside className="right-col" aria-label="智能分析">
          <section className="card agent-card"><AgentChat compact onTraceUpdated={() => setTraceVersion((value) => value + 1)} /></section>
          <section className="card trace-card"><TracePanel recent compact version={traceVersion} /></section>
        </aside>
      </div>
      <p className="footnote">StockPilot V0.1 · 行情信息仅供参考，不构成投资建议</p>
    </main>
  );
}
