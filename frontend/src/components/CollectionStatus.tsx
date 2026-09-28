import { cachedTime } from "../lib/format";
import type { CollectionState, StockCollectionStatus } from "../lib/types";

export const collectionLabel: Record<CollectionState, string> = {
  warming: "预热中", ready: "已更新", stale: "旧缓存", partial: "部分数据不可用", unavailable: "数据暂不可用",
};

export function CollectionBadge({ state, title }: { state: CollectionState; title?: string }) {
  return <span className={`collection-badge ${state}`} title={title}>{collectionLabel[state]}</span>;
}

export function CollectionStatus({ status, retry, busy }: {
  status: StockCollectionStatus; retry: () => void; busy: boolean;
}) {
  const labels = { quote: "报价", intraday: "分时", daily: "日 K", weekly: "周 K" };
  return <section className="card collection-card" aria-label="自选股采集状态">
    <div className="section-head"><div><h2>自选股采集状态</h2><span>后台自动更新 · 打开页面优先显示缓存</span></div><button className="text-button" disabled={busy} onClick={retry}>{busy ? "正在请求…" : "重试更新"}</button></div>
    <div className="collection-grid">{(Object.keys(labels) as Array<keyof typeof labels>).map((resource) => {
      const item = status.resources[resource];
      return <div key={resource}><strong>{labels[resource]}</strong><CollectionBadge state={item.state} />
        <small>{item.cached_at ? cachedTime(item.cached_at).replace(/^\s*·\s*/, "") : "暂无成功缓存"}</small>
        {item.source && <small>来源：{item.source === "tencent" ? "腾讯财经" : "东方财富"}</small>}
      </div>;
    })}</div>
  </section>;
}
