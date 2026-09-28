"use client";

import { useState } from "react";
import Link from "next/link";
import { apiRequest, errorText } from "../lib/api";
import { cachedTime } from "../lib/format";
import type { NewsPage } from "../lib/types";
import { useResource } from "../lib/use-resource";
import { CollectionBadge } from "./CollectionStatus";

function published(value: string) {
  return new Date(value).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", hour12: false });
}

export function NewsPanel({ symbol }: { symbol?: string }) {
  const [page, setPage] = useState(1);
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [keyword, setKeyword] = useState("");
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const base = symbol ? `/api/stocks/${symbol}/news` : "/api/market/news";
  const params = new URLSearchParams({ page: String(page), page_size: "10" });
  if (start) params.set("start", start);
  if (end) params.set("end", end);
  if (query) params.set("keyword", query);
  const news = useResource<NewsPage>(`${base}?${params}`, 10000);
  const refresh = async () => {
    setBusy(true); setNote("");
    try {
      await apiRequest(`${base}/refresh`, { method: "POST" });
      setNote("已请求更新，成功后自动显示；已有新闻继续保留。");
      news.refresh();
    } catch (error) { setNote(errorText(error)); }
    finally { setBusy(false); }
  };
  const title = symbol ? "个股新闻" : "市场新闻";
  return <section className="card news-card" aria-label={title}>
    <div className="section-head"><div><h2>{title}</h2><span>{symbol ? "代码关键词关联" : "东方财富 · 证券要闻"} · 后台缓存 5 分钟</span></div>
      <button className="text-button" disabled={busy} onClick={() => void refresh()}>{busy ? "正在请求…" : "更新新闻"}</button></div>
    <form className="news-filters" onSubmit={(event) => { event.preventDefault(); setQuery(keyword.trim()); setPage(1); }}>
      <label>起始日期<input type="date" value={start} onChange={(event) => { setStart(event.target.value); setPage(1); }} /></label>
      <label>结束日期<input type="date" value={end} onChange={(event) => { setEnd(event.target.value); setPage(1); }} /></label>
      <label>关键词<input value={keyword} maxLength={100} placeholder="搜索标题或来源片段" onChange={(event) => setKeyword(event.target.value)} /></label>
      <button className="text-button" type="submit">筛选</button>
    </form>
    {news.collectionState && <p className="news-state"><CollectionBadge state={news.collectionState} />{news.cachedAt && <span>{cachedTime(news.cachedAt)}（采集时间）</span>}</p>}
    {news.error && <p className="inline-error" role="alert">新闻读取失败：{news.error}<button className="text-button" onClick={news.refresh}>重试读取</button></p>}
    {note && <p className="table-note" role="status">{note}</p>}
    {news.loading ? <div className="section-state">正在加载新闻…</div> : !news.data?.items.length && !news.error ?
      <div className="section-state">{news.collectionState === "warming" ? "后台正在预热新闻，成功后自动显示。" : news.collectionState === "unavailable" ? "新闻源暂不可用，暂无成功缓存。" : "已采集范围内暂无符合条件的新闻。"}</div> : null}
    <ul className="news-list">{news.data?.items.map((item) => <li key={item.id}>
      <a href={item.url} target="_blank" rel="noopener noreferrer">{item.title}<span aria-hidden="true"> ↗</span></a>
      <div className="news-meta"><span>{item.source}</span><time dateTime={item.published_at}>{published(item.published_at)}（发布时间）</time></div>
      {item.excerpt && <p className="news-excerpt">{item.excerpt}</p>}
      <div className="news-meta"><span>{item.excerpt ? "来源片段 · 非全文" : "仅有标题 · 正文不可用"}</span>
        {item.symbols.map((stock) => <Link key={stock} href={`/stock/${stock}`}>{stock}</Link>)}<a href={item.url} target="_blank" rel="noopener noreferrer">查看原文</a></div>
    </li>)}</ul>
    {news.data && <>
      {news.data.partial && <p className="stale-note">本次仅获取到部分新闻，其他已缓存内容仍保留。</p>}
      {news.stale && <p className="stale-note">正在显示旧新闻缓存，请结合发布时间阅读。</p>}
      <p className="table-note">{news.data.coverage}。{symbol && "关键词关联不代表公司公告或确定的涨跌原因。"}</p>
      <div className="news-pagination"><button className="text-button" disabled={page <= 1 || news.loading} onClick={() => setPage((value) => value - 1)}>上一页</button>
        <span>第 {page} 页 · 缓存内 {news.data.total} 条</span><button className="text-button" disabled={!news.data.has_more || news.loading} onClick={() => setPage((value) => value + 1)}>加载下一页</button></div>
    </>}
  </section>;
}
