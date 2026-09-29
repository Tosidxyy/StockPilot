"use client";

import { useResource } from "../lib/use-resource";
import { number } from "../lib/format";
import { ResourceStatus } from "./ResourceStatus";
import type { MarketBreadth } from "../lib/types";

const names = { SH: "上证 A 股", SZ: "深证 A 股", BJ: "北交所" };
function sourceTime(value: string | null): string {
  return value ? new Date(value).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", hour12: false }) : "来源时间不可用";
}

export function MarketBreadthPanel() {
  const resource = useResource<MarketBreadth>("/api/market/breadth", 30000);
  const data = resource.data;
  return <section className="card temperature-card" aria-label="市场温度">
    <div className="section-head"><div><h2>市场温度</h2><span>沪深京 A 股 · 每 30 秒查询</span></div><button className="text-button" onClick={resource.refresh}>重试更新</button></div>
    <ResourceStatus resource={resource} name="市场统计" hasData={data !== null} hasContent={data !== null} partial={data?.partial} empty="暂无已采集的市场统计。" />
    {data && <>
      <div className="overview-grid">
        <div className="mini"><span>上涨</span><strong className="up">{number(data.advancing, 0)}</strong></div>
        <div className="mini"><span>下跌</span><strong className="down">{number(data.declining, 0)}</strong></div>
        <div className="mini"><span>平盘</span><strong>{number(data.unchanged, 0)}</strong></div>
        <div className="mini"><span>统计日期</span><strong className="breadth-date">{data.date || "无法同日合计"}</strong></div>
        <div className="mini"><span>涨停股池（来源口径）</span><strong className="up">{number(data.limit_up.count, 0)}</strong><small>{data.limit_up.date || "日期不可用"}{data.limit_up.stale ? " · 旧股池" : ""}</small></div>
        <div className="mini"><span>跌停股池（来源口径）</span><strong className="down">{number(data.limit_down.count, 0)}</strong><small>{data.limit_down.date || "日期不可用"}{data.limit_down.stale ? " · 旧股池" : ""}</small></div>
      </div>
      <div className="breadth-markets">{data.exchanges.map((item) => <div key={item.exchange}><strong>{names[item.exchange]}{item.stale || resource.stale ? " · 旧值" : ""}</strong><span>涨 {number(item.advancing, 0)} · 跌 {number(item.declining, 0)} · 平 {number(item.unchanged, 0)}</span><small>来源截至 {sourceTime(item.as_of)}（北京时间）</small></div>)}</div>
      {data.partial && <p className="card-note">三市场计数不齐、不同日或包含旧值时不显示完整合计；各市场与股池时间分别列示。</p>}
      <p className="card-note">来源：东方财富 · {data.scope}</p>
      <p className="card-note">{data.definition}</p>
      <p className="card-note">涨跌停为对应股池数量，覆盖未保证与上述范围一致，不能相加；股池只有统计日期，没有来源秒级更新时间。</p>
      <p className="card-note"><a href="https://quote.eastmoney.com/stockhotmap/" target="_blank" rel="noopener noreferrer">涨跌统计原页 ↗</a> · <a href="https://quote.eastmoney.com/ztb/" target="_blank" rel="noopener noreferrer">股池原页 ↗</a></p>
    </>}
  </section>;
}
