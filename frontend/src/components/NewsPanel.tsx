"use client";

import { useState } from "react";
import Link from "next/link";
import { apiRequest, errorText } from "../lib/api";
import type { NewsPage } from "../lib/types";
import { useResource } from "../lib/use-resource";
import { ResourceStatus } from "./ResourceStatus";
import { useListFilters } from "../lib/use-list-filters";

function published(value: string) {
  return new Date(value).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", hour12: false });
}

export function NewsPanel({ symbol }: { symbol?: string }) {
  const filters = useListFilters();
  const { draft, change, page, setPage, params } = filters;
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const base = symbol ? `/api/stocks/${symbol}/news` : "/api/market/news";
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
    <form className="news-filters" aria-label={`${title}筛选`} onSubmit={filters.apply}>
      <label>起始日期<input type="date" value={draft.start} onChange={(event) => change("start", event.target.value)} /></label>
      <label>结束日期<input type="date" value={draft.end} onChange={(event) => change("end", event.target.value)} /></label>
      <label>关键词<input value={draft.keyword} maxLength={100} placeholder="搜索标题或来源片段" onChange={(event) => change("keyword", event.target.value)} /></label>
      <button className="text-button" type="submit">筛选</button>
      <button className="text-button" type="button" onClick={filters.clear}>清除筛选</button>
    </form>
    {filters.issue && <p className="inline-error" role="alert">{filters.issue}</p>}
    <ResourceStatus resource={news} name={title} hasData={news.data !== null} hasContent={Boolean(news.data?.items.length)} partial={news.data?.partial}
      empty="已采集范围内暂无符合条件的新闻。" />
    {note && <p className="table-note" role="status">{note}</p>}
    <ul className="news-list">{news.data?.items.map((item) => <li key={item.id}>
      <a href={item.url} target="_blank" rel="noopener noreferrer">{item.title}<span aria-hidden="true"> ↗</span></a>
      <div className="news-meta"><span>{item.source}</span><time dateTime={item.published_at}>{published(item.published_at)}（发布时间）</time></div>
      {item.excerpt && <p className="news-excerpt">{item.excerpt}</p>}
      <div className="news-meta"><span>{item.excerpt ? "来源片段 · 非全文" : "仅有标题 · 正文不可用"}</span>
        {item.symbols.map((stock) => <Link key={stock} href={`/stock/${stock}`}>{stock}</Link>)}<a href={item.url} target="_blank" rel="noopener noreferrer">查看原文</a></div>
    </li>)}</ul>
    {news.data && <>
      <p className="table-note">{news.data.coverage}。{symbol && "关键词关联不代表公司公告或确定的涨跌原因。"}</p>
      <nav className="news-pagination" aria-label={`${title}分页`}><button className="text-button" disabled={page <= 1 || news.loading} onClick={() => setPage((value) => value - 1)}>上一页</button>
        <span role="status">第 {page} 页 · 缓存内 {news.data.total} 条</span><button className="text-button" disabled={!news.data.has_more || news.loading} onClick={() => setPage((value) => value + 1)}>加载下一页</button></nav>
    </>}
  </section>;
}
