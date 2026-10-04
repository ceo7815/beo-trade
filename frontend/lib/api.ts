export type FeedStatus = {
  provider: string;
  authenticated: boolean | null;
  last_success: string | null;
  last_fetch: string | null;
  quote_count?: number;
  option_count?: number;
  newest_quote?: string | null;
  last_error: string | null;
};

export type SystemStatus = {
  app: string;
  market_phase: string;
  now_exchange: string;
  now_display: string;
  session_open: string | null;
  session_close: string | null;
  providers: Record<string, string>;
  market_status?: FeedStatus | null;
  options_status?: FeedStatus | null;
  news_status?: {
    provider: string;
    authenticated: boolean | null;
    last_success: string | null;
    last_fetch: string | null;
    article_count: number;
    last_error: string | null;
  } | null;
  macro_status?: {
    provider: string;
    authenticated: boolean | null;
    last_success: string | null;
    last_fetch: string | null;
    series_count: number;
    last_error: string | null;
  } | null;
  research_status?: {
    provider: string;
    authenticated: boolean | null;
    last_success: string | null;
    last_fetch: string | null;
    filing_count: number;
    fact_count: number;
    submissions_status: number | null;
    facts_status: number | null;
    last_error: string | null;
  } | null;
  internal_candidates: number;
  active_buys: number;
  paper_equity: string;
  paper_cash: string;
  equity_basis: string;
  ai_month_usd: string;
  ai_day_usd: string;
  ai_hour_usd: string;
  ai_hard_limit: string;
  ai_soft_budget: string;
  ai_critical_budget: string;
  ai_calls: number;
  ai_avg_cost_per_buy: string | null;
  ai_mode: string;
  ai_model: string;
  health: string;
  database?: string;
  worker_heartbeat_age_seconds?: number | null;
  alerts: string[];
  paper_only: boolean;
  trading_halted?: boolean;
};

export type BuyCard = {
  recommendation_id: string;
  timestamp: string;
  underlying: string;
  underlying_price: string;
  option_symbol: string;
  call_put: "CALL" | "PUT";
  strike: string;
  expiration: string;
  option_price: string;
  max_entry_price: string;
  quantity: number;
  holding_window_min: number;
  holding_window_max: number;
  why: string[];
  thesis: string;
  catalyst: string;
  risk: string;
  invalidation: string;
};

export type BuyDetail = BuyCard & {
  bid: string;
  ask: string;
  delta: string;
  gamma: string;
  theta: string;
  vega: string;
  iv: string;
  volume: number;
  open_interest: number;
  scenarios: { move_percent: number; option_price: number; change_percent: number }[];
  greek_notes: string[];
};

export type PaperAccount = {
  cash: string;
  starting_cash: string;
  equity: string;
  realized_pnl: string;
  open_cost: string;
  return_percent: string;
  open_positions: number;
  trades: {
    trade_id: string;
    recommendation_id: string;
    option_symbol: string;
    quantity: number;
    entry_price: string;
    entry_at: string;
    exit_price: string | null;
    exit_reason: string;
    pnl_dollars: string | null;
    pnl_percent: string | null;
  }[];
};

export const PHASES: Record<string, string> = {
  CLOSED: "השוק סגור",
  PRE_MARKET: "טרום מסחר",
  OPEN: "פתיחה",
  INTRADAY_SCAN: "מסחר פעיל",
  PRE_CLOSE: "לפני סגירה",
  POST_MARKET: "אחרי סגירה",
  DAILY_REPORT: "סיכום יומי",
};

export async function apiGet<T>(path: string): Promise<T> {
  const response = await fetch(`/backend${path}`, { cache: "no-store" });
  if (!response.ok) {
    throw new Error("הבקשה נכשלה");
  }
  return response.json() as Promise<T>;
}

export async function apiDelete(path: string): Promise<void> {
  const response = await fetch(`/backend${path}`, { method: "DELETE" });
  if (!response.ok) throw new Error("הבקשה נכשלה");
}

export async function apiPost<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(`/backend${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(typeof payload.detail === "string" ? payload.detail : "הבקשה נכשלה");
  }
  return payload as T;
}
