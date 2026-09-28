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

export type IntradayPoint = {
  time: string;
  price: number;
  volume: number | null;
  turnover: number | null;
  source: "eastmoney" | "tencent";
};

export type StockQuote = {
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
