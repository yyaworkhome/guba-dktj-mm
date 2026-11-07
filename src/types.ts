export type Kind = "market" | "index" | "stock" | "sector" | "portfolio";
export interface Instrument {
  demo_available?: boolean;
  id: string;
  code: string;
  name: string;
  kind: Kind;
  exchange: string;
  scope: {
    boards?: string[];
    breadth?: string;
    members?: string[];
    universe?: string;
  };
}
export interface Metrics {
  score: number | null;
  coverage: number;
  state: string;
  dimensions: Record<string, number | null>;
  bullish_pct: number;
  bearish_pct: number;
  neutral_pct: number;
  bullish: number;
  bearish: number;
  neutral: number;
  posts: number;
  comments: number;
  reads: number;
  consistency: number;
  disagreement: number;
  ratio: number | null;
  heat_change: number;
}
export interface Point {
  date: string;
  score: number;
  bullish_pct: number;
  close: number | null;
}
export interface Signal {
  name: string;
  tone: string;
  reason: string;
}
export interface Analysis {
  instrument: Instrument;
  data_mode: string;
  source: string;
  as_of: string;
  period: number;
  requested_period: number;
  range: string[];
  metrics: Metrics;
  latest: Metrics;
  trend: Point[];
  signals: Signal[];
  keywords: { text: string; count: number }[];
  limitations: string[];
  score_version: string;
}
export interface Ranking extends Instrument, Metrics {
  date: string;
}
export interface BacktestRow {
  horizon: number;
  samples: number;
  excluded: number;
  mean_return: number | null;
  up_pct: number | null;
  ci_low: number | null;
  ci_high: number | null;
}
export interface Backtest {
  results: BacktestRow[];
  limitations: string[];
  method: string;
  threshold: number;
}
export interface Alert {
  id: number;
  instrument: Instrument;
  metric: string;
  threshold: number;
  value: number;
  triggered: boolean;
}

export interface LiveAnalysis {
  instrument_id: string; name: string; code: string; content: string; source_url: string; collector_command: string;
  examples: { source_id: string; excerpt: string; url: string; label: string; published_at: string; first_seen_at: string; observed_at: string }[];
  as_of: string; last_post_observation: string | null; stale: boolean; days: number; range: string[];
  sampled_posts: number; effective_posts: number; authors: number; opinion_score: number | null; coverage: number;
  counts: Record<string, number>; percentages: Record<string, number | null>;
  comments: number; reads: number; removed: Record<string, number>;
  keywords: { text: string; count: number }[];
  trend: { date: string; posts: number; opinion_score: number | null }[];
  quote: { observed_at: string; bars: { date: string; open: number; close: number; is_current_session: boolean }[] } | null;
  status: { latest_run: { id: number; status: string; finished_at: string | null;
    details: Record<string, { status?: string; error?: string; accepted?: number; received?: number; requested?: number; detail_excluded?: unknown[] }> } | null };
  interpretation?: {
    status: string; headline: string; direction_coverage: number | null;
    quality_reasons: string[]; bullets: string[]; method: string;
    price_observation: { from_date: string; to_date: string; close: number; change_pct: number; observed_at: string } | null;
  };
  limitations: string[];
}
