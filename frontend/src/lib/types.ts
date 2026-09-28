export type DataEnvelope<T> = { data: T; stale: boolean; cached_at?: string | null };

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
  volume: number;
  turnover: number;
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
  turnover: number;
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
