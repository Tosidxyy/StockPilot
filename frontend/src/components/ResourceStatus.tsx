import { cachedTime } from "../lib/format";
import type { CollectionState } from "../lib/types";
import { CollectionBadge } from "./CollectionStatus";

type Resource = { loading: boolean; error: string | null; stale: boolean; cachedAt: string | null; collectionState: CollectionState | null; refresh: () => void };

export function ResourceStatus({ resource, name, hasData, hasContent, partial = false, empty }: {
  resource: Resource; name: string; hasData: boolean; hasContent: boolean; partial?: boolean; empty: string;
}) {
  const state = resource.stale ? "stale" : partial ? "partial" : resource.collectionState ?? (hasData ? "ready" : null);
  return <div className="resource-status">
    {(state || resource.cachedAt) && <div className="news-state">{state && <CollectionBadge state={state} />}
      {resource.cachedAt && <span>{cachedTime(resource.cachedAt).replace(/^\s*·\s*/, "")}（采集保存时间）</span>}</div>}
    {resource.loading && <p className="section-state" role="status">正在读取{name}…</p>}
    {resource.error && <p className="inline-error" role="alert">{name}读取失败：{resource.error}
      <button className="text-button" onClick={resource.refresh}>重试读取</button></p>}
    {!resource.loading && !resource.error && !hasContent && <p className="section-state" role="status">
      {state === "warming" ? `${name}正在准备，完成后自动显示。` : state === "unavailable" ? `${name}暂不可用，暂无成功缓存。` : empty}
      {state === "unavailable" && <button className="text-button" onClick={resource.refresh}>重试读取</button>}</p>}
    {resource.stale && hasData && <p className="stale-note" role="status">正在显示{name}旧缓存；采集成功后自动更新，请结合资料时间阅读。</p>}
    {partial && <p className="stale-note" role="status">{name}仅有部分数据，已成功的内容继续显示；缺失不代表零。</p>}
  </div>;
}
