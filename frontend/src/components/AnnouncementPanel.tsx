"use client";

import { useState } from "react";
import { apiRequest, errorText } from "../lib/api";
import { cachedTime } from "../lib/format";
import type { AnnouncementItem, AnnouncementPage } from "../lib/types";
import { useResource } from "../lib/use-resource";
import { ResourceStatus } from "./ResourceStatus";
import { useListFilters } from "../lib/use-list-filters";

const textLabels = { pending: "正文准备中", ready: "附件文本已提取", partial: "仅部分正文", unavailable: "正文不可用" };

function AnnouncementText({ symbol, item }: { symbol: string; item: AnnouncementItem }) {
  const body = useResource<AnnouncementItem>(`/api/stocks/${symbol}/announcements/${item.id}`, 10000);
  const document = body.data;
  return <div className="announcement-body">
    <ResourceStatus resource={body} name="公告正文" hasData={document !== null} hasContent={Boolean(document?.text)} empty="暂无已缓存正文，请查看原文附件。" />
    {document && <>
      <p className="table-note">{textLabels[document.text_status]} · {document.text_length} 字
        {document.total_pages !== null && ` · 已处理 ${document.pages_extracted}/${document.total_pages} 页`}
        {document.text_fetched_at && ` ${cachedTime(document.text_fetched_at)}（正文保存时间）`}</p>
      {document.text_reason && <p className="stale-note">{document.text_reason}</p>}
      {document.text_stale && <p className="stale-note">此正文是此前缓存，尚未确认最新版本。</p>}
      {document.text_status === "pending" && <p className="table-note">正文正在准备，完成后自动显示。</p>}
      {document.text && <pre className="announcement-text" tabIndex={0} aria-label="公告提取正文">{document.text}</pre>}
      <div className="news-meta">{document.attachment_urls.map((url, index) => <a key={url} href={url} target="_blank" rel="noopener noreferrer">附件 {index + 1} ↗</a>)}</div>
    </>}
  </div>;
}

export function AnnouncementPanel({ symbol }: { symbol: string }) {
  const filters = useListFilters();
  const { draft, change, page, setPage, params } = filters;
  const [open, setOpen] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const base = `/api/stocks/${symbol}/announcements`;
  const announcements = useResource<AnnouncementPage>(`${base}?${params}`, 10000);
  const update = async () => {
    setBusy(true); setNote("");
    try {
      await apiRequest(`${base}/refresh`, { method: "POST" });
      setNote("已请求更新，成功后自动显示；已有公告继续保留。");
      announcements.refresh();
    } catch (error) { setNote(errorText(error)); }
    finally { setBusy(false); }
  };
  return <section className="card news-card" aria-label="公司公告">
    <div className="section-head"><div><h2>公司公告</h2><span>东方财富 · 每 10 分钟更新</span></div>
      <button className="text-button" disabled={busy} onClick={() => void update()}>{busy ? "正在请求…" : "更新公告"}</button></div>
    <form className="news-filters" aria-label="公司公告筛选" onSubmit={(event) => { if (filters.apply(event)) setOpen(null); }}>
      <label>公告起始日期<input type="date" value={draft.start} onChange={(event) => change("start", event.target.value)} /></label>
      <label>公告结束日期<input type="date" value={draft.end} onChange={(event) => change("end", event.target.value)} /></label>
      <label>公告类型<select value={draft.category} onChange={(event) => change("category", event.target.value)}>
        <option value="">全部类型</option>{announcements.data?.categories.map((item) => <option key={item.code} value={item.code}>{item.name}</option>)}</select></label>
      <label>公告关键词<input value={draft.keyword} maxLength={100} placeholder="搜索公告标题" onChange={(event) => change("keyword", event.target.value)} /></label>
      <button className="text-button" type="submit">筛选公告</button>
      <button className="text-button" type="button" onClick={() => { filters.clear(); setOpen(null); }}>清除筛选</button>
    </form>
    {filters.issue && <p className="inline-error" role="alert">{filters.issue}</p>}
    <ResourceStatus resource={announcements} name="公司公告" hasData={announcements.data !== null} hasContent={Boolean(announcements.data?.items.length)} partial={announcements.data?.partial}
      empty="已采集范围内暂无符合条件的公告。" />
    {note && <p className="table-note" role="status">{note}</p>}
    <ul className="news-list">{announcements.data?.items.map((item) => <li key={item.id}>
      <a href={item.url} target="_blank" rel="noopener noreferrer">{item.title} ↗</a>
      <div className="news-meta"><span>{item.source}</span><time dateTime={item.notice_date}>{item.notice_date}（公告日期）</time>
        {item.disclosed_at && <time dateTime={item.disclosed_at}>{new Date(item.disclosed_at).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", hour12: false })}（平台披露）</time>}
        <span>{item.categories.map((value) => value.name).join(" / ") || "未分类"}</span></div>
      <div className="news-meta"><span>{textLabels[item.text_status]}{item.text_stale && " · 旧正文"}</span>
        <a href={item.url} target="_blank" rel="noopener noreferrer">查看原文</a>
        <button className="text-button" aria-expanded={open === item.id} aria-controls={`announcement-${item.id}`} onClick={() => setOpen(open === item.id ? null : item.id)}>{open === item.id ? "收起正文" : "查看缓存正文"}</button></div>
      {open === item.id && <div id={`announcement-${item.id}`}><AnnouncementText symbol={symbol} item={item} /></div>}
    </li>)}</ul>
    {announcements.data && <>
      <p className="table-note">{announcements.data.coverage}。附件为报告摘要时不代表完整报告；表格排版可能与原 PDF 不同。</p>
      <nav className="news-pagination" aria-label="公司公告分页"><button className="text-button" disabled={page <= 1 || announcements.loading} onClick={() => { setPage(page - 1); setOpen(null); }}>上一页</button>
        <span role="status">第 {page} 页 · 缓存内 {announcements.data.total} 条</span><button className="text-button" disabled={!announcements.data.has_more || announcements.loading} onClick={() => { setPage(page + 1); setOpen(null); }}>加载下一页</button></nav>
    </>}
  </section>;
}
