import { copy, phases } from "./copy";
import { holdScreen, unavailable } from "./holdScreen";
import type { RegimeStatus } from "./regime";

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
  paper_equity: string | null;
  paper_cash: string;
  equity_basis: string;
  regime_status?: RegimeStatus | null;
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
  monitor_last_success?: string | null;
  monitor_cycle_ok?: boolean | null;
  alerts: string[];
  paper_only: boolean;
  trading_halted?: boolean;
  daily_kill_loss_usd?: string | null;
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

export const PHASES: Record<string, string> = phases;

export type LiveMode = "connecting" | "sse" | "poll";
let liveMode: LiveMode = "connecting";

export function transportMode() {
  return liveMode;
}

export function setTransportMode(mode: LiveMode) {
  liveMode = mode;
}

export function seedCache(path: string, data: unknown) {
  memory.set(path, { data, at: Date.now() });
}

function toLogin(response: Response) {
  if (response.status !== 401 || typeof window === "undefined" || window.location.pathname === "/login") return;
  window.location.href = `/login?next=${encodeURIComponent(window.location.pathname + window.location.search)}`;
}

type CacheEntry = { data: unknown; at: number };

const memory = new Map<string, CacheEntry>();
const flights = new Map<string, Promise<unknown>>();

function windowFor(path: string) {
  if (path.includes("/report/trades") || path.includes("/learning/") || path.includes("/finance")) {
    return { fresh: 20_000, stale: 180_000 };
  }
  if (
    path.includes("/broker/alpaca/positions") ||
    path.includes("/broker/alpaca/orders") ||
    path.includes("/broker/alpaca/account") ||
    path.includes("/risk") ||
    path.includes("/desk")
  ) {
    return { fresh: 8_000, stale: 60_000 };
  }
  if (path.includes("/system") || path.includes("/release")) {
    return { fresh: 12_000, stale: 90_000 };
  }
  return { fresh: 8_000, stale: 45_000 };
}

export { holdScreen, unavailable };

function cachedEntry(path: string): CacheEntry | null {
  const hit = memory.get(path);
  if (!hit) return null;
  const expired = Date.now() - hit.at > windowFor(path).stale;
  if (expired && unavailable(hit.data)) {
    memory.delete(path);
    return null;
  }
  return hit;
}

export function peekCached<T>(path: string): T | null {
  const hit = cachedEntry(path);
  return hit ? (hit.data as T) : null;
}

function fetchFresh<T>(path: string, timeoutMs: number): Promise<T> {
  const existing = flights.get(path);
  if (existing) return existing as Promise<T>;
  const promise = (async () => {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const response = await fetch(`/backend${path}`, { cache: "no-store", signal: controller.signal });
      toLogin(response);
      if (!response.ok) throw new Error(copy.errors.request);
      const data = (await response.json()) as T;
      if (unavailable(data)) {
        const previous = memory.get(path);
        if (previous && !unavailable(previous.data)) {
          const prior = previous.data as { available?: boolean; stale?: boolean; as_of?: string | null };
          const kept = holdScreen(prior, data as typeof prior);
          if (kept) kept.as_of = prior.as_of ?? new Date(previous.at).toISOString();
          memory.set(path, { data: kept, at: previous.at });
          return kept as T;
        }
      }
      memory.set(path, { data, at: Date.now() });
      return data;
    } finally {
      clearTimeout(timer);
      flights.delete(path);
    }
  })();
  flights.set(path, promise);
  return promise;
}

export async function apiGet<T>(path: string, timeoutMs = 15000): Promise<T> {
  const hit = cachedEntry(path);
  const age = hit ? Date.now() - hit.at : Infinity;
  const { fresh } = windowFor(path);
  if (hit && age < fresh) return hit.data as T;
  if (hit) {
    void fetchFresh(path, timeoutMs).catch(() => undefined);
    return hit.data as T;
  }
  return fetchFresh<T>(path, timeoutMs);
}

/** Show the last payload immediately, then replace it when a refresh lands. */
export function loadCached<T>(path: string, apply: (body: T) => void, timeoutMs = 15000): Promise<void> {
  const hit = cachedEntry(path);
  const age = hit ? Date.now() - hit.at : Infinity;
  const { fresh } = windowFor(path);
  if (hit) apply(hit.data as T);
  if (hit && age < fresh) return Promise.resolve();
  return fetchFresh<T>(path, timeoutMs).then(apply);
}

export function warm(paths: string[]) {
  for (const path of paths) void apiGet(path).catch(() => undefined);
}

export function poll(task: () => Promise<unknown> | unknown, everyMs: number): () => void {
  let stopped = false;
  let busy = false;
  let timer: ReturnType<typeof setTimeout> | undefined;
  const schedule = (delay: number) => {
    if (!stopped) timer = setTimeout(run, delay);
  };
  const run = async () => {
    if (stopped || busy) return;
    if (typeof document !== "undefined" && document.hidden) {
      schedule(1000);
      return;
    }
    busy = true;
    try {
      await task();
    } catch {
      /* the screen keeps the last good payload */
    } finally {
      busy = false;
      schedule(everyMs);
    }
  };
  const wake = () => {
    if (typeof document !== "undefined" && document.hidden) return;
    if (busy) return;
    clearTimeout(timer);
    void run();
  };
  document.addEventListener("visibilitychange", wake);
  window.addEventListener("online", wake);
  window.addEventListener("focus", wake);
  void run();
  return () => {
    stopped = true;
    clearTimeout(timer);
    document.removeEventListener("visibilitychange", wake);
    window.removeEventListener("online", wake);
    window.removeEventListener("focus", wake);
  };
}

export async function apiDelete(path: string): Promise<void> {
  const response = await fetch(`/backend${path}`, { method: "DELETE" });
  toLogin(response);
  if (!response.ok) throw new Error(copy.errors.request);
}

export async function apiPost<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(`/backend${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  toLogin(response);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(typeof payload.detail === "string" ? payload.detail : copy.errors.request);
  }
  return payload as T;
}
