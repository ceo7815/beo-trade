/** Active trading-period / account selection for the header switcher. */

export type TradingPeriodRow = {
  id: string;
  label: string;
  start?: string;
  end?: string | null;
  capital_basis_usd?: string | null;
  is_current?: boolean;
  trading?: boolean;
  read_only?: boolean;
  has_view_keys?: boolean;
  view_mode?: string;
  alpaca_account_name?: string | null;
};

const STORAGE_KEY = "beo.trading_period";
const PARAM = "account";

export function readStoredPeriod(): string {
  if (typeof window === "undefined") return "current";
  try {
    const fromQuery = new URLSearchParams(window.location.search).get(PARAM);
    if (fromQuery) return fromQuery;
    return window.localStorage.getItem(STORAGE_KEY) || "current";
  } catch {
    return "current";
  }
}

export function writeStoredPeriod(id: string) {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(STORAGE_KEY, id);
    const url = new URL(window.location.href);
    if (id === "current") url.searchParams.delete(PARAM);
    else url.searchParams.set(PARAM, id);
    window.history.replaceState({}, "", url.toString());
  } catch {
    /* ignore */
  }
}

/** Append trading_period to API paths that should respect the switcher. */
export function withTradingPeriod(path: string, period: string): string {
  if (!period || period === "current") {
    // Still attach for risk/desk/broker so the backend knows explicitly.
  }
  const targets = [
    "/api/v1/risk",
    "/api/v1/desk",
    "/api/v1/finance",
    "/api/v1/report/trades",
    "/api/v1/learning/trades",
    "/api/v1/learning/lab",
    "/api/v1/broker/alpaca/positions",
    "/api/v1/broker/alpaca/orders",
    "/api/v1/broker/alpaca/account",
    "/api/v1/trading-rules",
  ];
  if (!targets.some((prefix) => path === prefix || path.startsWith(prefix + "?") || path.startsWith(prefix + "/"))) {
    return path;
  }
  const join = path.includes("?") ? "&" : "?";
  if (/[?&]trading_period=/.test(path)) return path;
  return `${path}${join}trading_period=${encodeURIComponent(period || "current")}`;
}
