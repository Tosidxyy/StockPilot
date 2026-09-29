"use client";

import { useState } from "react";
import Link from "next/link";
import { apiRequest, errorText } from "../lib/api";
import { amount, cachedTime, moveClass, number, signed, updatedTime } from "../lib/format";
import type { CollectionOverview, IntradayPoint, KlineItem, StockQuote, WatchlistEntry } from "../lib/types";
import { useResource } from "../lib/use-resource";
import { KlineChart, StockIntradayChart } from "./Charts";
import { CollectionStatus } from "./CollectionStatus";
import { NewsPanel } from "./NewsPanel";
import { AnnouncementPanel } from "./AnnouncementPanel";
import { MoneyFlowPanel } from "./MoneyFlowPanel";

function exchange(symbol: string): string {
  if (symbol.startsWith("6")) return "上交所";
  if (symbol.startsWith("4") || symbol.startsWith("8")) return "北交所";
  return "深交所";
}

export function StockDetail({ code }: { code: string }) {
  const [period, setPeriod] = useState<"intraday" | "daily" | "weekly">("intraday");
  const [saving, setSaving] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [retrying, setRetrying] = useState(false);
  const [retryNote, setRetryNote] = useState("");
  const watchlist = useResource<WatchlistEntry[]>("/api/watchlist", 5000);
  const saved = watchlist.data?.some((entry) => entry.symbol === code) ?? false;
  const collection = useResource<CollectionOverview>(saved ? "/api/watchlist/status" : null, 2000);
  const collectionStatus = collection.data?.items.find((item) => item.symbol === code);
  const quote = useResource<StockQuote>(`/api/stocks/${code}/quote`, 2000);
  const intraday = useResource<IntradayPoint[]>(period === "intraday" ? `/api/stocks/${code}/intraday` : null, 2000);
  const kline = useResource<KlineItem[]>(period !== "intraday" ? `/api/stocks/${code}/kline?period=${period}&limit=120` : null, saved ? 2000 : 60000);
  const chart = period === "intraday" ? intraday : kline;
  const retry = async () => {
    setRetrying(true);
    setActionError(null);
    try {
      if (saved && collection.data?.enabled) {
        await apiRequest(`/api/watchlist/${code}/refresh`, { method: "POST" });
        setRetryNote("已请求后台重试，成功后自动更新，已有缓存继续显示。");
      }
      quote.refresh(); intraday.refresh(); kline.refresh(); collection.refresh();
    } catch (error) {
      setActionError(errorText(error));
    } finally {
      setRetrying(false);
    }
  };

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
      quote.refresh(); intraday.refresh(); kline.refresh(); collection.refresh();
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
      {saved && collection.data?.enabled && collectionStatus && <CollectionStatus status={collectionStatus} retry={() => void retry()} busy={retrying} />}
      {saved && collection.error && <p className="table-note">采集状态暂不可用，已有行情继续显示。</p>}
      {retryNote && <p className="table-note" role="status">{retryNote}</p>}

      <section className="card quote-card" aria-labelledby="quote-title">
        <div className="section-head"><div><h2 id="quote-title">基础行情</h2><span>每 2 秒刷新{!quote.stale && updatedTime(quote.cachedAt)}</span></div>{quote.stale && <span className="stale-pill">旧缓存</span>}</div>
        {quote.loading ? <div className="quote-main skeleton" /> :
          quote.error && !data ? <div className="section-state error-state">{quote.error}<button disabled={retrying} onClick={() => void retry()}>重试</button></div> :
            !data ? <div className="section-state">{quote.collectionState === "warming" ? "后台正在预热报价，成功后自动显示。" : quote.collectionState === "unavailable" ? "后台暂未获取到报价，正在等待重试。" : "暂无该股票行情。"}<button className="text-button" disabled={retrying} onClick={() => void retry()}>重试更新</button></div> : <>
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
        {quote.stale && data && <p className="stale-note">行情暂未更新，正在显示旧缓存{cachedTime(quote.cachedAt)}。<button className="text-button" disabled={retrying} onClick={() => void retry()}>重试更新</button></p>}
        {data && <p className="table-note">报价来源：{data.source === "tencent" ? "腾讯财经" : data.source === "sina" ? "新浪财经" : "东方财富"} · {data.as_of ? `成交时间 ${data.as_of.slice(0, 19).replace("T", " ")}（北京时间）` : "源成交时间未提供"}</p>}
      </section>

      <section className="card kline-card" aria-labelledby="kline-title">
        <div className="section-head"><div><h2 id="kline-title">{period === "intraday" ? "实时分时与成交量" : "K 线与成交量"}</h2><span>{period === "intraday" ? `最新交易日 · 每 2 秒刷新${intraday.data?.length ? ` · 更新至 ${intraday.data.at(-1)?.time.replace("T", " ")}` : ""}` : "不复权 · 最近 120 根"}</span></div><div className="period-tabs" role="group" aria-label="行情周期"><button className={period === "intraday" ? "active" : ""} type="button" onClick={() => setPeriod("intraday")}>分时</button><button className={period === "daily" ? "active" : ""} type="button" onClick={() => setPeriod("daily")}>日 K</button><button className={period === "weekly" ? "active" : ""} type="button" onClick={() => setPeriod("weekly")}>周 K</button></div></div>
        {chart.loading ? <div className="chart kline-chart skeleton" /> :
          (chart.error || chart.collectionState === "unavailable") && !chart.data?.length ? <div className="section-state error-state">{period === "intraday" ? "分时数据暂不可用，可查看历史日 K。" : "历史 K 线暂不可用，请稍后重试。"}<button disabled={retrying} onClick={() => void retry()}>重试</button>{period === "intraday" && <button onClick={() => setPeriod("daily")}>查看历史日 K</button>}</div> :
            !chart.data?.length ? <div className="section-state">{chart.collectionState === "warming" ? "后台正在预热此周期数据，成功后自动显示。" : "暂无该周期行情数据。"}</div> :
              <div className="chart-wrap">{period === "intraday" ? <StockIntradayChart points={intraday.data!} name={data?.name || code} /> : <KlineChart items={kline.data!} />}</div>}
        {chart.stale && <p className="stale-note">行情暂未更新，正在显示旧缓存{cachedTime(chart.cachedAt)}。<button className="text-button" disabled={retrying} onClick={() => void retry()}>重试更新</button></p>}
        {period !== "intraday" && kline.data?.length && <p className="table-note">历史 K 线来源：{kline.data[0].source === "tencent" ? "腾讯财经（备用源，未提供历史成交额）" : "东方财富"} · 数据截至 {kline.data.at(-1)?.date} · 当日/本周尚未收盘的数据可能变化</p>}
        {period === "intraday" && Boolean(intraday.data?.length) && <p className="table-note">分时来源：{intraday.data![0].source === "sina" ? "新浪财经" : intraday.data![0].source === "tencent" ? "腾讯财经（备用源）" : "东方财富（备用源）"} · 常规交易时段 · 成交量单位：手 · 成交额单位：元{intraday.data![0].source === "sina" && " · 1 分钟 K 线收盘价，末根可能未完成"}</p>}
        {period === "intraday" && intraday.data?.some((point) => point.volume === null || point.turnover === null) && <p className="table-note">部分分钟缺失，无法还原该段成交量或成交额；空值不代表零。</p>}
      </section>

      <div className="stock-secondary stock-news-section">
        <MoneyFlowPanel key={`flows-${code}`} symbol={code} />
        <NewsPanel key={code} symbol={code} />
        <AnnouncementPanel key={`announcements-${code}`} symbol={code} />
      </div>
      <Link className="card agent-shortcut" href={`/agent?q=${encodeURIComponent(`分析 ${data?.name || code}（${code}）的最新行情与最近五天走势`)}`}><span className="agent-icon">✦</span><div><h2>AI 快捷分析</h2><p>基于这只股票的真实行情与 K 线向 Agent 提问。</p></div><span className="chip">前往对话</span></Link>
      <p className="footnote">StockPilot V0.1 · 行情信息仅供参考，不构成投资建议</p>
    </main>
  );
}
