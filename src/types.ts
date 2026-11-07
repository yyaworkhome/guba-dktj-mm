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
