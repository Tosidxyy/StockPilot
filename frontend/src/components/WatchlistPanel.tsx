"use client";

import { useState } from "react";
import Link from "next/link";
import { apiRequest, errorText } from "../lib/api";
import { amount, cachedTime, moveClass, number, signed, updatedTime } from "../lib/format";
import type { CollectionOverview, StockQuote, StockSearchResult, WatchlistEntry } from "../lib/types";
import { useResource } from "../lib/use-resource";
import { StockSearch } from "./StockSearch";
import { CollectionBadge, collectionLabel } from "./CollectionStatus";

export function WatchlistPanel() {
  const entries = useResource<WatchlistEntry[]>("/api/watchlist", 5000);
  const codes = entries.data?.map((entry) => entry.symbol).join(",") || "";
  const quotes = useResource<StockQuote[]>(codes ? "/api/watchlist/quotes" : null, 2000);
  const collection = useResource<CollectionOverview>(codes ? "/api/watchlist/status" : null, 2000);
  const statusMap = new Map(collection.data?.items.map((item) => [item.symbol, item]) ?? []);
  const [retrying, setRetrying] = useState(false);
  const [retryNote, setRetryNote] = useState("");
  const [adding, setAdding] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [changeSort, setChangeSort] = useState<"none" | "descending" | "ascending">("none");
  const quoteMap = new Map(quotes.data?.map((quote) => [quote.symbol, quote]) || []);
  const sortedEntries = [...(entries.data ?? [])];
  if (changeSort !== "none") {
    sortedEntries.sort((a, b) => {
      const left = quoteMap.get(a.symbol)?.change_percent;
      const right = quoteMap.get(b.symbol)?.change_percent;
      const leftMissing = left == null || !Number.isFinite(left);
      const rightMissing = right == null || !Number.isFinite(right);
      if (leftMissing || rightMissing) return Number(leftMissing) - Number(rightMissing);
      return changeSort === "descending" ? right! - left! : left! - right!;
    });
  }
  const nextSort = changeSort === "none" ? "descending" : changeSort === "descending" ? "ascending" : "none";
  const sortAction = nextSort === "descending" ? "按涨跌幅从高到低排序" : nextSort === "ascending" ? "按涨跌幅从低到高排序" : "恢复添加顺序";

  const add = async (item: StockSearchResult) => {
    setBusy(item.symbol);
    setActionError(null);
    try {
      await apiRequest<WatchlistEntry>("/api/watchlist", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ symbol: item.symbol }),
      });
      entries.refresh();
      quotes.refresh();
      collection.refresh();
      setAdding(false);
    } catch (error) {
      setActionError(errorText(error));
    } finally {
      setBusy(null);
    }
  };

  const remove = async (symbol: string) => {
    setBusy(symbol);
    setActionError(null);
    try {
      await apiRequest<void>(`/api/watchlist/${symbol}`, { method: "DELETE" });
      entries.refresh();
      quotes.refresh();
      collection.refresh();
    } catch (error) {
      setActionError(errorText(error));
    } finally {
      setBusy(null);
    }
  };

  const retry = async () => {
    setRetrying(true);
    setActionError(null);
    try {
      if (collection.data?.enabled) {
        await apiRequest("/api/watchlist/refresh", { method: "POST" });
        setRetryNote("已请求后台重试，成功后自动更新。暂时失败时保留已有缓存。");
      }
      quotes.refresh();
      collection.refresh();
    } catch (error) {
      setActionError(errorText(error));
    } finally {
      setRetrying(false);
    }
  };

  return (
    <section className="card watch-card" id="watchlist" aria-labelledby="watchlist-title">
      <div className="section-head">
        <div><h2 id="watchlist-title">我的自选股</h2><span>{entries.data ? `${entries.data.length} 只` : "加载中"} · 每 2 秒刷新{collection.data?.enabled ? " · 后台自动采集" : ""}{!quotes.stale && updatedTime(quotes.cachedAt)}</span></div>
        <div>{codes && collection.data?.enabled && <button className="text-button" disabled={retrying} onClick={() => void retry()}>重试更新</button>}<button className="text-button" type="button" onClick={() => setAdding(!adding)}>{adding ? "收起" : "+ 添加股票"}</button></div>
      </div>
      {adding && <div className="watch-add"><StockSearch onSelect={add} placeholder="搜索代码或名称后添加" autoFocus />{busy && <span className="muted-text">正在添加…</span>}</div>}
      {actionError && <p className="inline-error" role="alert">{actionError}</p>}
      {entries.loading ? <div className="section-state">正在加载自选股…</div> :
        entries.error && !entries.data ? <div className="section-state error-state">{entries.error}<button onClick={entries.refresh}>重试</button></div> :
          !entries.data?.length ? <div className="section-state">自选股为空。点击“添加股票”开始关注。</div> :
            <div className="table-scroll">
              <table className="watch-table">
                <thead><tr><th>股票</th><th>现价</th><th aria-sort={changeSort}><button className="table-sort" type="button" onClick={() => setChangeSort(nextSort)} aria-label={`涨跌幅排序：${sortAction}`} title={sortAction}>涨跌幅 <span aria-hidden="true">{changeSort === "descending" ? "↓" : changeSort === "ascending" ? "↑" : "↕"}</span></button></th><th>成交额</th><th>换手率</th><th><span className="sr-only">操作</span></th></tr></thead>
                <tbody>{sortedEntries.map((entry) => {
                  const quote = quoteMap.get(entry.symbol);
                  const status = statusMap.get(entry.symbol);
                  return <tr key={entry.symbol}>
                    <td><Link className="stock-name" href={`/stock/${entry.symbol}`}><strong>{quote?.name || entry.symbol}</strong><span>{entry.symbol}</span></Link>{collection.data?.enabled && status && <CollectionBadge state={status.state} title={`报价：${collectionLabel[status.resources.quote.state]}；分时：${collectionLabel[status.resources.intraday.state]}；日 K：${collectionLabel[status.resources.daily.state]}；周 K：${collectionLabel[status.resources.weekly.state]}`} />}</td>
                    <td>{number(quote?.price)}</td>
                    <td className={moveClass(quote?.change_percent)}>{signed(quote?.change_percent)}</td>
                    <td>{amount(quote?.turnover)}</td>
                    <td>{signed(quote?.turnover_rate)}</td>
                    <td><button className="row-action" type="button" disabled={busy === entry.symbol} onClick={() => void remove(entry.symbol)} aria-label={`移除 ${entry.symbol}`}>移除</button></td>
                  </tr>;
                })}</tbody>
              </table>
            </div>}
      {codes && (quotes.loading || quotes.error || quotes.stale || quotes.collectionState === "warming" || quotes.collectionState === "unavailable" || quotes.collectionState === "partial") &&
        <p className={`table-note ${quotes.error ? "inline-error" : ""}`}>
          {quotes.loading ? "正在获取自选股行情…" : quotes.stale ? `行情暂未更新，正在显示旧缓存${cachedTime(quotes.cachedAt)}。` : quotes.collectionState === "warming" ? "后台正在预热自选股行情，成功后自动显示。" : quotes.collectionState === "partial" ? "部分股票暂无可用报价，已有行情继续显示，后台持续重试。" : quotes.collectionState === "unavailable" ? "后台暂未获取到报价，正在等待重试。" : `行情更新失败：${quotes.error}`}
          {!quotes.loading && <button className="text-button" disabled={retrying} onClick={() => void retry()}>重试更新</button>}
        </p>}
      {retryNote && <p className="table-note" role="status">{retryNote}</p>}
      {collection.error && <p className="table-note">采集状态暂不可用，已有行情继续显示。</p>}
      {entries.stale && <p className="table-note">自选股列表暂未更新，当前展示上次结果。</p>}
    </section>
  );
}
