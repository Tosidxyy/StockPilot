"use client";

import { useState } from "react";
import Link from "next/link";
import { apiRequest, errorText } from "../lib/api";
import { amount, cachedTime, moveClass, number, signed, updatedTime } from "../lib/format";
import type { StockQuote, StockSearchResult, WatchlistEntry } from "../lib/types";
import { useResource } from "../lib/use-resource";
import { StockSearch } from "./StockSearch";

export function WatchlistPanel() {
  const entries = useResource<WatchlistEntry[]>("/api/watchlist");
  const codes = entries.data?.map((entry) => entry.symbol).join(",") || "";
  const quotes = useResource<StockQuote[]>(codes ? `/api/stocks/quotes?codes=${encodeURIComponent(codes)}` : null, 2000);
  const [adding, setAdding] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const quoteMap = new Map(quotes.data?.map((quote) => [quote.symbol, quote]) || []);

  const add = async (item: StockSearchResult) => {
    setBusy(item.symbol);
    setActionError(null);
    try {
      await apiRequest<WatchlistEntry>("/api/watchlist", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ symbol: item.symbol }),
      });
      entries.refresh();
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
    } catch (error) {
      setActionError(errorText(error));
    } finally {
      setBusy(null);
    }
  };

  return (
    <section className="card watch-card" id="watchlist" aria-labelledby="watchlist-title">
      <div className="section-head">
        <div><h2 id="watchlist-title">我的自选股</h2><span>{entries.data ? `${entries.data.length} 只` : "加载中"} · 每 2 秒刷新{!quotes.stale && updatedTime(quotes.cachedAt)}</span></div>
        <button className="text-button" type="button" onClick={() => setAdding(!adding)}>{adding ? "收起" : "+ 添加股票"}</button>
      </div>
      {adding && <div className="watch-add"><StockSearch onSelect={add} placeholder="搜索代码或名称后添加" autoFocus />{busy && <span className="muted-text">正在添加…</span>}</div>}
      {actionError && <p className="inline-error" role="alert">{actionError}</p>}
      {entries.loading ? <div className="section-state">正在加载自选股…</div> :
        entries.error && !entries.data ? <div className="section-state error-state">{entries.error}<button onClick={entries.refresh}>重试</button></div> :
          !entries.data?.length ? <div className="section-state">自选股为空。点击“添加股票”开始关注。</div> :
            <div className="table-scroll">
              <table className="watch-table">
                <thead><tr><th>股票</th><th>现价</th><th>涨跌幅</th><th>成交额</th><th>换手率</th><th><span className="sr-only">操作</span></th></tr></thead>
                <tbody>{entries.data.map((entry) => {
                  const quote = quoteMap.get(entry.symbol);
                  return <tr key={entry.symbol}>
                    <td><Link className="stock-name" href={`/stock/${entry.symbol}`}><strong>{quote?.name || entry.symbol}</strong><span>{entry.symbol}</span></Link></td>
                    <td>{number(quote?.price)}</td>
                    <td className={moveClass(quote?.change_percent)}>{signed(quote?.change_percent)}</td>
                    <td>{amount(quote?.turnover)}</td>
                    <td>{signed(quote?.turnover_rate)}</td>
                    <td><button className="row-action" type="button" disabled={busy === entry.symbol} onClick={() => void remove(entry.symbol)} aria-label={`移除 ${entry.symbol}`}>移除</button></td>
                  </tr>;
                })}</tbody>
              </table>
            </div>}
      {codes && (quotes.loading || quotes.error || quotes.stale) &&
        <p className={`table-note ${quotes.error ? "inline-error" : ""}`}>
          {quotes.loading ? "正在获取自选股行情…" : quotes.stale ? `行情暂未更新，正在显示旧缓存${cachedTime(quotes.cachedAt)}。` : `行情更新失败：${quotes.error}`}
          {!quotes.loading && <button className="text-button" onClick={quotes.refresh}>重试更新</button>}
        </p>}
      {entries.stale && <p className="table-note">自选股列表暂未更新，当前展示上次结果。</p>}
    </section>
  );
}
