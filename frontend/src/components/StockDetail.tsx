"use client";

import { useState } from "react";
import Link from "next/link";
import { apiRequest, errorText } from "../lib/api";
import { amount, cachedTime, moveClass, number, signed } from "../lib/format";
import type { IntradayPoint, KlineItem, StockQuote, WatchlistEntry } from "../lib/types";
import { useResource } from "../lib/use-resource";
import { KlineChart, StockIntradayChart } from "./Charts";

function exchange(symbol: string): string {
  if (symbol.startsWith("6")) return "上交所";
  if (symbol.startsWith("4") || symbol.startsWith("8")) return "北交所";
  return "深交所";
}

export function StockDetail({ code }: { code: string }) {
  const [period, setPeriod] = useState<"intraday" | "daily" | "weekly">("intraday");
  const [saving, setSaving] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const quote = useResource<StockQuote>(`/api/stocks/${code}/quote`, 10000);
  const intraday = useResource<IntradayPoint[]>(period === "intraday" ? `/api/stocks/${code}/intraday` : null, 2000);
  const kline = useResource<KlineItem[]>(period !== "intraday" ? `/api/stocks/${code}/kline?period=${period}&limit=120` : null, 60000);
  const chart = period === "intraday" ? intraday : kline;
  const watchlist = useResource<WatchlistEntry[]>("/api/watchlist");
  const saved = watchlist.data?.some((entry) => entry.symbol === code) ?? false;

  const toggleWatchlist = async () => {
    setSaving(true);
    setActionError(null);
    try {
      if (saved) {
        await apiRequest<void>(`/api/watchlist/${code}`, { method: "DELETE" });
      } else {
        await apiRequest<WatchlistEntry>("/api/watchlist", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ symbol: code }),
        });
      }
      watchlist.refresh();
    } catch (error) {
      setActionError(errorText(error));
    } finally {
      setSaving(false);
    }
  };

  const data = quote.data;
  return (
    <main className="content stock-content">
      <nav className="breadcrumb" aria-label="当前位置"><Link href="/">市场概览</Link><span>/</span><span>个股详情</span></nav>
      <div className="stock-hero">
        <div><p className="eyebrow">STOCKPILOT / STOCK DETAIL</p><h1>{data?.name || code}</h1><p>{code} · {exchange(code)}</p></div>
        <button className={`outline-button ${saved ? "saved" : ""}`} type="button" disabled={saving || watchlist.loading || Boolean(watchlist.error && !watchlist.data)} onClick={() => void toggleWatchlist()}>{saving ? "处理中…" : saved ? "✓ 已加入自选" : "+ 加入自选"}</button>
      </div>
      {actionError && <p className="inline-error" role="alert">{actionError}</p>}
      {watchlist.error && !watchlist.data && <p className="inline-error" role="alert">自选股状态获取失败：{watchlist.error} <button onClick={watchlist.refresh}>重试</button></p>}

      <section className="card quote-card" aria-labelledby="quote-title">
        <div className="section-head"><div><h2 id="quote-title">基础行情</h2><span>来自行情数据源</span></div>{quote.stale && <span className="stale-pill">旧缓存</span>}</div>
        {quote.loading ? <div className="quote-main skeleton" /> :
          quote.error && !data ? <div className="section-state error-state">{quote.error}<button onClick={quote.refresh}>重试</button></div> :
            !data ? <div className="section-state">暂无该股票行情。</div> : <>
              <div className="quote-main"><strong className={moveClass(data.change_percent)}>{number(data.price)}</strong><span className={moveClass(data.change_percent)}>{signed(data.change_amount, "")} · {signed(data.change_percent)}</span></div>
              <div className="quote-metrics">
                <div className="metric"><span>今开</span><strong>{number(data.open)}</strong></div>
                <div className="metric"><span>最高</span><strong>{number(data.high)}</strong></div>
                <div className="metric"><span>最低</span><strong>{number(data.low)}</strong></div>
                <div className="metric"><span>昨收</span><strong>{number(data.previous_close)}</strong></div>
                <div className="metric"><span>成交量</span><strong>{number(data.volume, 0)}</strong></div>
                <div className="metric"><span>成交额</span><strong>{amount(data.turnover)}</strong></div>
                <div className="metric"><span>换手率</span><strong>{signed(data.turnover_rate)}</strong></div>
                <div className="metric"><span>市盈率</span><strong>{number(data.pe_ratio)}</strong></div>
              </div>
            </>}
        {quote.stale && data && <p className="stale-note">行情暂未更新，正在显示旧缓存{cachedTime(quote.cachedAt)}。<button className="text-button" onClick={quote.refresh}>重试更新</button></p>}
      </section>

      <section className="card kline-card" aria-labelledby="kline-title">
        <div className="section-head"><div><h2 id="kline-title">{period === "intraday" ? "实时分时与成交量" : "K 线与成交量"}</h2><span>{period === "intraday" ? `最新交易日 · 每 2 秒刷新${intraday.data?.length ? ` · 更新至 ${intraday.data.at(-1)?.time.replace("T", " ")}` : ""}` : "不复权 · 最近 120 根"}</span></div><div className="period-tabs" role="group" aria-label="行情周期"><button className={period === "intraday" ? "active" : ""} type="button" onClick={() => setPeriod("intraday")}>分时</button><button className={period === "daily" ? "active" : ""} type="button" onClick={() => setPeriod("daily")}>日 K</button><button className={period === "weekly" ? "active" : ""} type="button" onClick={() => setPeriod("weekly")}>周 K</button></div></div>
        {chart.loading ? <div className="chart kline-chart skeleton" /> :
          chart.error && !chart.data ? <div className="section-state error-state">{chart.error}<button onClick={chart.refresh}>重试</button></div> :
            !chart.data?.length ? <div className="section-state">暂无该周期行情数据。</div> :
              <div className="chart-wrap">{period === "intraday" ? <StockIntradayChart points={intraday.data!} name={data?.name || code} /> : <KlineChart items={kline.data!} />}</div>}
        {chart.stale && <p className="stale-note">行情暂未更新，正在显示旧缓存{cachedTime(chart.cachedAt)}。<button className="text-button" onClick={chart.refresh}>重试更新</button></p>}
      </section>

      <div className="stock-secondary">
        <section className="card placeholder-card"><div className="section-head"><div><h2>资金流向</h2><span>P1 数据待接入</span></div></div><div className="section-state">当前暂无资金流接口，接入后展示真实资金数据。</div></section>
        <section className="card placeholder-card"><div className="section-head"><div><h2>个股新闻</h2><span>P1 数据待接入</span></div></div><div className="section-state">新闻数据源尚未接入。</div></section>
      </div>
      <Link className="card agent-shortcut" href={`/agent?q=${encodeURIComponent(`分析 ${data?.name || code}（${code}）的最新行情与最近五天走势`)}`}><span className="agent-icon">✦</span><div><h2>AI 快捷分析</h2><p>基于这只股票的真实行情与 K 线向 Agent 提问。</p></div><span className="chip">前往对话</span></Link>
      <p className="footnote">StockPilot V0.1 · 行情信息仅供参考，不构成投资建议</p>
    </main>
  );
}
