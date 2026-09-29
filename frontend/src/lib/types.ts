export type CollectionState = "warming" | "ready" | "stale" | "partial" | "unavailable";

export type NewsItem = {
  id: string; title: string; source: string; published_at: string; url: string;
  excerpt: string | null; content_available: boolean; symbols: string[]; association: string;
};
export type NewsPage = {
  items: NewsItem[]; total: number; page: number; page_size: number; has_more: boolean;
  source_truncated: boolean; partial: boolean; coverage: string; collection_state: CollectionState;
};
export type DataEnvelope<T> = { data: T; stale: boolean; cached_at?: string | null; collection_state?: CollectionState | null };
export type AnnouncementCategory = { code: string; name: string };
export type AnnouncementItem = {
  id: string; title: string; notice_date: string; disclosed_at: string | null; source: string; url: string;
  symbols: string[]; categories: AnnouncementCategory[]; attachment_urls: string[];
  text_status: "pending" | "ready" | "partial" | "unavailable"; text: string | null; text_source: string | null;
  text_reason: string | null; text_hash: string | null; source_text_hash: string | null; text_length: number;
  pages_extracted: number; total_pages: number | null; text_fetched_at: string | null; text_stale: boolean;
};
export type AnnouncementPage = {
  items: AnnouncementItem[]; categories: AnnouncementCategory[]; total: number; page: number; page_size: number;
  has_more: boolean; source_truncated: boolean; partial: boolean; coverage: string; collection_state: CollectionState;
};
export type ResourceCollectionStatus = {
  state: CollectionState;
  cached_at: string | null;
  last_attempt_at: string | null;
  source: string | null;
};
export type StockCollectionStatus = {
  symbol: string;
  state: CollectionState;
  resources: Record<"quote" | "intraday" | "daily" | "weekly", ResourceCollectionStatus>;
};
export type CollectionOverview = { enabled: boolean; items: StockCollectionStatus[] };

export type MarketIndex = {
  symbol: string;
  name: string;
  value: number | null;
  change_percent: number | null;
  change_amount: number | null;
  volume: number | null;
  turnover: number | null;
  high: number | null;
  low: number | null;
};

export type MarketBreadth = {
  source: "eastmoney"; scope: string; definition: string;
  advancing: number | null; declining: number | null; unchanged: number | null;
  date: string | null; partial: boolean; counts_complete: boolean;
  exchanges: { exchange: "SH" | "SZ" | "BJ"; advancing: number | null; declining: number | null;
    unchanged: number | null; as_of: string | null; stale: boolean; complete: boolean }[];
  limit_up: { count: number | null; date: string | null; stale: boolean; scope: string };
  limit_down: { count: number | null; date: string | null; stale: boolean; scope: string };
};

export type MoneyFlow = {
  date: string;
  main_net: number | null; super_large_net: number | null; large_net: number | null;
  medium_net: number | null; small_net: number | null;
  main_ratio: number | null; super_large_ratio: number | null; large_ratio: number | null;
  medium_ratio: number | null; small_ratio: number | null;
};

export type MoneyFlowSeries = {
  symbol: string; source: "eastmoney"; amount_unit: "CNY"; ratio_unit: "percent";
  definition: string; coverage: string; items: MoneyFlow[];
};

export type IntradayPoint = {
  time: string;
  price: number;
  volume: number | null;
  turnover: number | null;
  source: "eastmoney" | "tencent";
};

export type StockQuote = {
  source?: "eastmoney" | "tencent" | "sina";
  as_of?: string | null;
  symbol: string;
  name: string;
  price: number | null;
  change_percent: number | null;
  change_amount: number | null;
  volume: number | null;
  turnover: number | null;
  high: number | null;
  low: number | null;
  open: number | null;
  previous_close: number | null;
  turnover_rate: number | null;
  pe_ratio: number | null;
};

export type KlineItem = {
  date: string;
  open: number;
  close: number;
  high: number;
  low: number;
  volume: number;
  turnover: number | null;
  source: "eastmoney" | "tencent";
};

export type StockSearchResult = {
  symbol: string;
  name: string;
  market: string;
};

export type WatchlistEntry = {
  symbol: string;
  added_at: string;
};

export type TraceEntry = {
  session_id: string;
  step_index: number;
  tool_name: string;
  tool_input: Record<string, string | number | boolean | null>;
  tool_output_summary: string;
  status: "success" | "error";
  latency_ms: number;
  created_at: string;
};
