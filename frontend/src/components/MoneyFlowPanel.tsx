"use client";

import { useState } from "react";
import { apiRequest, errorText } from "../lib/api";
import { amount, cachedTime, moveClass, signed } from "../lib/format";
import { useResource } from "../lib/use-resource";
import type { MoneyFlowSeries } from "../lib/types";
import { CollectionBadge } from "./CollectionStatus";

const groups = [
  ["主力", "main_net", "main_ratio"], ["超大单", "super_large_net", "super_large_ratio"],
  ["大单", "large_net", "large_ratio"], ["中单", "medium_net", "medium_ratio"], ["小单", "small_net", "small_ratio"],
] as const;

function net(value: number | null): string {
  return value === null ? "暂无数据" : `${value < 0 ? "−" : value > 0 ? "+" : ""}${amount(Math.abs(value))} 元`;
}

export function MoneyFlowPanel({ symbol }: { symbol: string }) {
  const resource = useResource<MoneyFlowSeries>(`/api/stocks/${symbol}/money-flow`, 10000);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const [expanded, setExpanded] = useState(false);
  const series = resource.data;
  const latest = series?.items.at(-1);
  const refresh = async () => {
    setBusy(true);
    try {
      await apiRequest(`/api/stocks/${symbol}/money-flow/refresh`, { method: "POST" });
      setNote("已请求更新，已有缓存继续显示；成功后自动更新。");
      resource.refresh();
    } catch (error) { setNote(errorText(error)); }
    finally { setBusy(false); }
  };
  const state = resource.stale ? "stale" : resource.collectionState;
  return <section className="card money-flow-card" aria-label="个股资金流向">
    <div className="section-head"><div><h2>资金流向</h2><span>最新可用统计日 · 日序列 · 开市时采集目标 60 秒</span></div><button className="text-button" disabled={busy} onClick={() => void refresh()}>{busy ? "正在请求…" : "更新资金流"}</button></div>
    <div className="news-state">{state && <CollectionBadge state={state} />}<span>来源：东方财富 · 金额：元 · 净占比：%</span><span>{cachedTime(resource.cachedAt)}</span><a href={`https://data.eastmoney.com/zjlx/${symbol}.html`} target="_blank" rel="noopener noreferrer">核对原始数据 ↗</a></div>
    {resource.loading && <p className="section-state">正在加载资金流…</p>}
    {resource.error && !series && <p className="section-state" role="alert">{resource.error} <button className="text-button" onClick={resource.refresh}>重试</button></p>}
    {series && !latest && <p className="section-state">{state === "warming" ? "后台正在预热资金流，无需打开详情触发采集。" : state === "unavailable" ? "资金流源暂不可用，尚无成功缓存。" : "来源返回空序列，暂无可用资金流记录。"}</p>}
    {latest && <>
      <p className="table-note">统计日期：{latest.date}（北京时间） · 交易日内该日数值可能继续变化；日期不是缓存保存时间。</p>
      <div className="money-flow-grid">{groups.map(([label, field, ratio]) => <div key={field}><span>{label}净流入</span><strong className={moveClass(latest[field])}>{net(latest[field])}</strong><small>净占比 {latest[ratio] === null ? "暂无数据" : signed(latest[ratio])}</small></div>)}</div>
      <button className="text-button" aria-expanded={expanded} onClick={() => setExpanded(!expanded)}>{expanded ? "收起历史资金流" : `查看近期 ${series!.items.length} 个交易日`}</button>
      {expanded && <div className="money-flow-table"><table><caption>近期资金流（最新日期在前；金额单位元）</caption><thead><tr><th scope="col">统计日期</th>{groups.map(([label]) => <th scope="col" key={label}>{label}净流入</th>)}</tr></thead><tbody>{[...series!.items].reverse().map((item) => <tr key={item.date}><th scope="row">{item.date}</th>{groups.map(([, field]) => <td key={field} className={moveClass(item[field])}>{net(item[field])}</td>)}</tr>)}</tbody></table></div>}
    </>}
    {resource.stale && <p className="stale-note">资金流暂未更新，正在显示旧缓存{cachedTime(resource.cachedAt)}。</p>}
    <p className="table-note">{series?.definition || "资金流按来源订单大小分类，不等于机构账户真实持仓或新增资金。"}</p>
    <p className="table-note">{series?.coverage || "最近最多30个交易日，不保证包含今天；缺失字段不估算，不显示为零。"}</p>
    {note && <p className="table-note" role="status">{note}</p>}
  </section>;
}
