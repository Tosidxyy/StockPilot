"use client";

import Link from "next/link";
import { ResourceStatus } from "./ResourceStatus";
import { useResource } from "../lib/use-resource";

type Opinion = "bullish" | "bearish" | "mixed" | "unknown";
type Report = {
  symbol: string; sample_count: number; post_count: number; reply_count: number; duplicate_count: number;
  counts: Record<Opinion, number>; sample_start: string | null; sample_end: string | null;
  partial: boolean; coverage: string; method: string;
  items: { id: string; text: string; published_at: string; url: string; kind: "post_title" | "reply"; sentiment: Opinion }[];
};
const labels: Record<Opinion, string> = { bullish: "偏多", bearish: "偏空", mixed: "多空混合", unknown: "未判定" };
const time = (value: string) => new Date(value).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", hour12: false });

export function SentimentPanel({ symbol }: { symbol: string }) {
  const opinion = useResource<Report>(`/api/stocks/${symbol}/sentiment`, 60000);
  const report = opinion.data;
  return <section className="card news-card sentiment-card" aria-label="股吧情绪">
    <div className="section-head"><div><h2>股吧情绪</h2><span>东方财富 · 公开用户发言 · 每分钟更新</span></div>
      <button className="text-button" onClick={opinion.refresh} disabled={opinion.loading}>刷新读取</button></div>
    <ResourceStatus resource={opinion} name="股吧情绪" hasData={report !== null} hasContent={Boolean(report?.sample_count)} partial={report?.partial}
      empty="已采集范围内暂无近24小时用户发言，不能据此判断情绪。" />
    {report && <>
      <div className="sentiment-counts">{(Object.keys(labels) as Opinion[]).map((label) => <div key={label} className={`sentiment-${label}`}>
        <span>{labels[label]}</span><strong>{report.counts[label]}</strong><small>{report.sample_count ? `${Math.round(report.counts[label] / report.sample_count * 100)}%` : "—"}</small>
      </div>)}</div>
      <p className="table-note">近24小时采集 {report.sample_count} 条去重样本（发帖标题 {report.post_count} · 回复 {report.reply_count}）。{report.duplicate_count > 0 && `已去除 ${report.duplicate_count} 条重复文本。`}</p>
      {report.sample_end && <p className="news-meta">最新发言 {time(report.sample_end)}（北京时间）</p>}
      <ul className="news-list">{report.items.slice(0, 8).map((item) => <li key={item.id}>
        <a href={item.url} target="_blank" rel="noopener noreferrer">{item.text}<span aria-hidden="true"> ↗</span></a>
        <div className="news-meta"><span>{item.kind === "reply" ? "用户回复片段" : "用户发帖标题 · 非正文"}</span><span>{labels[item.sentiment]}</span><time dateTime={item.published_at}>{time(item.published_at)}</time></div>
      </li>)}</ul>
      <p className="table-note">{report.coverage}</p>
      <details className="sentiment-method"><summary>统计方法</summary><p>{report.method} 样本有选择偏差；用户发言未经事实核验，不能直接作为买卖信号。</p></details>
      <Link className="text-button" href={`/agent?q=${encodeURIComponent(`分析 ${symbol} 的股吧用户情绪，结合实际行情给出简短判断`)}`}>让 AI 结合行情解读 ↗</Link>
    </>}
  </section>;
}
