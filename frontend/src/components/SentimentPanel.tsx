"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { ResourceStatus } from "./ResourceStatus";
import { SentimentPieChart } from "./Charts";
import { useResource } from "../lib/use-resource";
import { ApiError, apiRequest } from "../lib/api";
import type { DataEnvelope } from "../lib/types";

type Emotion = "positive" | "negative" | "neutral";
type Report = {
  sample_count: number; post_count: number; reply_count: number; duplicate_count: number;
  sample_end: string | null; partial: boolean; coverage: string;
  items: { id: string; text: string; published_at: string; url: string; kind: "post_title" | "reply" }[];
};
type Result = {
  model: string; counts: Record<Emotion, number>; labels: Record<string, Emotion>;
  sample_count: number; reused_count: number; source_cached_at: string; source_stale: boolean;
  sample_start: string; sample_end: string; analysed_at: string;
  preview: { id: string; text: string; published_at: string; url: string; kind: "post_title" | "reply"; sentiment: Emotion }[];
};
type Analysis = {
  status: "idle" | "running" | "ready" | "failed" | "unconfigured";
  job_id: string | null; completed: number; total: number; error: string | null; result: Result | null;
  checked_at: string;
};
const analysisPolling = (view: Analysis | null) => !view || view.status === "running" ? 1000 : 0;
const labels: Record<Emotion, string> = { positive: "积极", negative: "消极", neutral: "中立" };
const time = (value: string) => new Date(value).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", hour12: false });

export function SentimentPanel({ symbol }: { symbol: string }) {
  return <SentimentContent key={symbol} symbol={symbol} />;
}

function SentimentContent({ symbol }: { symbol: string }) {
  const opinion = useResource<Report>(`/api/stocks/${symbol}/sentiment`, 60000);
  const [submitted, setSubmitted] = useState<Analysis | null>(null);
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState<string | null>(null);
  const mounted = useRef(true);
  const path = `/api/stocks/${symbol}/sentiment/analysis`;
  const analysis = useResource<Analysis>(path, analysisPolling);
  const report = opinion.data;
  const view = submitted && (!analysis.data || new Date(analysis.data.checked_at).getTime() <
    new Date(submitted.checked_at).getTime()) ? submitted : analysis.data;
  const result = view?.result;
  const running = starting || view?.status === "running";

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

  const start = async () => {
    if (running) return;
    setStarting(true);
    setStartError(null);
    try {
      const response = await apiRequest<DataEnvelope<Analysis>>(path, { method: "POST" });
      if (!mounted.current) return;
      setSubmitted(response.data);
      analysis.refresh();
    } catch (error) {
      if (mounted.current) setStartError(error instanceof ApiError && error.status === 409
        ? "当前没有可用的近24小时股吧样本，请先等待样本采集。"
        : error instanceof ApiError && error.status === 503
          ? "请先在后端配置 DeepSeek API Key。"
          : "无法启动情绪统计，请稍后重试。");
    } finally {
      if (mounted.current) setStarting(false);
    }
  };

  const outdated = result && (result.source_stale || opinion.stale || Boolean(opinion.cachedAt &&
    new Date(opinion.cachedAt).getTime() !== new Date(result.source_cached_at).getTime()) ||
    new Date(view?.checked_at || result.analysed_at).getTime() - new Date(result.analysed_at).getTime() > 86400000);

  return <section className="card news-card sentiment-card" aria-label="股吧情绪">
    <div className="section-head"><div><h2>股吧情绪</h2><span>东方财富 · 近24小时公开用户发言</span></div>
      <button className="text-button" onClick={opinion.refresh} disabled={opinion.loading}>刷新样本</button></div>
    <ResourceStatus resource={opinion} name="股吧情绪" hasData={report !== null} hasContent={Boolean(report?.sample_count)} partial={report?.partial}
      empty="已采集范围内暂无近24小时用户发言，不能据此判断情绪。" />
    <div className="sentiment-action">
      <button className="outline-button" onClick={() => void start()} disabled={running || !report?.sample_count}>
        {running ? "正在统计情绪…" : "统计当前股民情绪"}
      </button>
      <span className="table-note">点击后使用 DeepSeek Flash 分类，刷新页面不会自动调用模型。</span>
    </div>
    <div role="status" aria-live="polite">
      {running && <p className="table-note">正在分类 {view?.completed ?? 0} / {view?.total || report?.sample_count || 0} 条，行情和导航仍可使用。</p>}
      {(startError || view?.error || analysis.error) && <p className="inline-error">{startError || view?.error || analysis.error}</p>}
      {view?.status === "unconfigured" && <p className="table-note">配置 DeepSeek API Key 后即可开始模型统计。</p>}
      {!result && !running && <p className="table-note">尚未完成模型统计。积极、消极、中立的占比将在统计成功后显示。</p>}
    </div>
    {result && <>
      {outdated && <p className="table-note">这是上次采集样本的统计结果，可点击按钮重新统计；不代表最新全部股民情绪。</p>}
      <div className="sentiment-summary">
        <SentimentPieChart counts={result.counts} />
        <div className="sentiment-legend">{(Object.keys(labels) as Emotion[]).map((label) => <div key={label} className={`sentiment-${label}`}>
          <span>{labels[label]}</span><strong>{result.counts[label]} 条</strong>
          <small>{(result.counts[label] / result.sample_count * 100).toFixed(1)}%</small>
        </div>)}</div>
      </div>
      <p className="table-note">{result.model} · 已分类 {result.sample_count} 条 · 复用 {result.reused_count} 条已有分类</p>
      <p className="news-meta">样本范围 {time(result.sample_start)} 至 {time(result.sample_end)}<br />
        样本采集 {time(result.source_cached_at)} · 模型统计 {time(result.analysed_at)}</p>
    </>}
    {report && <>
      <p className="table-note">近24小时采集 {report.sample_count} 条去重样本（发帖标题 {report.post_count} · 回复 {report.reply_count}）。{report.duplicate_count > 0 && `已去除 ${report.duplicate_count} 条重复文本。`}</p>
      <p className="table-note">{report.coverage}</p>
    </>}
    <p className="table-note">{result ? "本次统计的样本（最多显示8条）" : "当前已采集的样本"}</p>
      <ul className="news-list">{(result?.preview ?? report?.items ?? []).slice(0, 8).map((item) => <li key={item.id}>
        <a href={item.url} target="_blank" rel="noopener noreferrer">{item.text}<span aria-hidden="true"> ↗</span></a>
        <div className="news-meta"><span>{item.kind === "reply" ? "用户回复片段" : "用户发帖标题 · 非正文"}</span>
          <span>{result?.labels[item.id] ? labels[result.labels[item.id]] : "未统计"}</span>
          <time dateTime={item.published_at}>{time(item.published_at)}</time></div>
      </li>)}</ul>
    <details className="sentiment-method"><summary>统计方法</summary>
      <p>用户点击后由 DeepSeek Flash 按积极、消极、中立分类可用样本。同一文本复用已有结果。
        未完成或失败的分类不计作中立。样本有选择偏差，用户发言未经事实核验，不代表全体投资者或涨跌预测。</p></details>
    <Link className="text-button" href={`/agent?q=${encodeURIComponent(`分析 ${symbol} 的股吧用户情绪，结合实际行情给出简短判断`)}`}>让 AI 结合行情解读 ↗</Link>
  </section>;
}
