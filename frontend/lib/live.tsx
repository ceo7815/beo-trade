"use client";

import { useEffect, useSyncExternalStore, type ReactNode } from "react";
import { seedCache, setTransportMode, type LiveMode } from "@/lib/api";
import { copy, exitLabel } from "@/lib/copy";
import { num, signedMoney } from "@/lib/format";

export type LivePosition = {
  symbol?: string;
  underlying?: string | null;
  right?: string | null;
  strike?: string | null;
  qty?: string | null;
  avg_entry_price?: string | null;
  current_price?: string | null;
  market_value?: string | null;
  cost_basis?: string | null;
  unrealized_pl?: string | null;
  unrealized_plpc?: string | null;
};

export type LiveAccount = {
  equity?: string | null;
  cash?: string | null;
  last_equity?: string | null;
  buying_power?: string | null;
  portfolio_value?: string | null;
};

export type LiveDay = {
  pnl?: string | null;
  mark_pnl?: string | null;
  pnl_source?: string | null;
  daily_loss_used?: string | null;
  daily_loss_limit?: string | null;
  daily_kill_loss_usd?: string | null;
  daily_loss_state?: string | null;
  open_positions?: number | null;
  day_floor?: { armed?: boolean; closed?: boolean; arm?: string | null; floor?: string | null; reason?: string | null } | null;
  day_return_pct?: string | null;
  day_invested_usd?: string | null;
  protection?: {
    status?: string | null;
    line?: string | null;
    kill?: string | null;
    pnl?: string | null;
    floor?: string | null;
    peak?: string | null;
    next_threshold?: string | null;
    next_floor?: string | null;
    distance?: string | null;
    protection_kind?: string | null;
    armed?: boolean;
  } | null;
};

export type LiveQuote = { symbol: string; price?: string | number | null; change_percent?: string | number | null };

export type LiveSnap = {
  ts?: string;
  positions?: { items: LivePosition[] } | null;
  account?: LiveAccount | null;
  day?: LiveDay | null;
  status?: {
    market_phase?: string;
    session_open?: string | null;
    session_close?: string | null;
    heartbeat_age_seconds?: number | null;
    trading_halted?: boolean;
    feeds?: { id: string; ok?: boolean; last_success?: string | null; age_seconds?: number | null }[];
  } | null;
  quotes?: LiveQuote[];
  scan?: { stage?: string | null; discovered?: number | null; filtered?: number | null; recommendations?: number | null; buys?: number | null; names?: string[] };
};

export type ToastItem = {
  id: string;
  title: string;
  tone: "ok" | "down" | "info" | "event";
};

type LiveState = {
  snap: LiveSnap | null;
  transport: LiveMode;
  stale: boolean;
  updatedAt: number | null;
  toasts: ToastItem[];
  series: Record<string, number[]>;
};

const EVENT_REASONS = new Set(["STOP", "PROTECTED_STOP", "TRAILING", "DAILY_HARD_STOP", "KILL_SWITCH", "DAY_FLOOR"]);

let state: LiveState = { snap: null, transport: "connecting", stale: false, updatedAt: null, toasts: [], series: {} };
const listeners = new Set<() => void>();
const seenTrades = new Set<string>();
let seededTrades = false;
const recentToast = new Map<string, number>();
let toastSeq = 0;

function emit() {
  listeners.forEach((listener) => listener());
}

function commit(next: Partial<LiveState>) {
  state = { ...state, ...next };
  emit();
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function snapshot() {
  return state;
}

export function useLive() {
  return useSyncExternalStore(subscribe, snapshot, snapshot);
}

function pushSeries(key: string, price: number | null) {
  if (price === null) return state.series[key] || [];
  const prev = state.series[key] || [];
  if (prev[prev.length - 1] === price) return prev;
  return [...prev, price].slice(-24);
}

function toast(key: string, title: string, tone: ToastItem["tone"]) {
  const now = Date.now();
  const last = recentToast.get(key) || 0;
  if (now - last < 8000) return;
  recentToast.set(key, now);
  const mobile = typeof window !== "undefined" && window.matchMedia("(max-width: 980px)").matches;
  const item: ToastItem = { id: `${now}-${toastSeq++}`, title, tone };
  commit({ toasts: [item, ...state.toasts].slice(0, 3) });
  window.setTimeout(() => {
    commit({ toasts: state.toasts.filter((row) => row.id !== item.id) });
  }, mobile ? 3000 : 4200);
}

function nameOf(item: { underlying?: string | null; right?: string | null; symbol?: string | null }) {
  const side = item.right === "PUT" || item.right === "CALL" ? item.right : "";
  return `${item.underlying || item.symbol || ""} ${side}`.trim();
}

function applySnap(incoming: LiveSnap, mode: "snapshot" | "diff") {
  const previous = state.snap;
  const next: LiveSnap = mode === "snapshot" || !previous ? incoming : { ...previous, ...incoming };
  const series = { ...state.series };
  for (const item of next.positions?.items || []) {
    const price = num(item.current_price);
    const key = item.symbol || item.underlying || "";
    if (key) series[key] = pushSeries(key, price);
  }
  for (const quote of next.quotes || []) {
    series[`q:${quote.symbol}`] = pushSeries(`q:${quote.symbol}`, num(quote.price));
  }
  if (next.account) series.eq = pushSeries("eq", num(next.account.equity));
  if (previous) {
    const before = new Map((previous.positions?.items || []).map((item) => [item.symbol || "", item]));
    const after = new Map((next.positions?.items || []).map((item) => [item.symbol || "", item]));
    for (const [symbol, item] of after) {
      if (symbol && !before.has(symbol)) toast(`entry:${symbol}`, copy.toasts.entry(nameOf(item)), "ok");
    }
    for (const [symbol, item] of before) {
      if (symbol && !after.has(symbol)) toast(`exit:${symbol}`, copy.toasts.exit(nameOf(item)), "info");
    }
    const was = previous.day?.daily_loss_state;
    const now = next.day?.daily_loss_state;
    if (now === "hard_stop" && was !== "hard_stop") toast("hard-stop", copy.toasts.hard, "event");
  }
  if (next.positions) seedCache("/api/v1/broker/alpaca/positions", { items: next.positions.items, environment: "PAPER" });
  if (next.account) seedCache("/api/v1/broker/alpaca/account", { account: next.account, environment: "PAPER" });
  commit({ snap: next, series, updatedAt: Date.now(), stale: false, transport: "sse" });
  setTransportMode("sse");
}

export function observeTrades(rows: { symbol?: string | null; underlying?: string | null; right?: string | null; exit_reason?: string | null; closed_at?: string | null; pnl?: string | null; opened_at?: string | null }[]) {
  const closed = rows.filter((row) => row.closed_at);
  if (!seededTrades) {
    for (const row of closed) seenTrades.add(`${row.symbol}|${row.closed_at}|${row.exit_reason || ""}`);
    seededTrades = true;
    return;
  }
  for (const row of closed) {
    const key = `${row.symbol}|${row.closed_at}|${row.exit_reason || ""}`;
    if (seenTrades.has(key)) continue;
    seenTrades.add(key);
    const reason = row.exit_reason || "";
    if (!EVENT_REASONS.has(reason)) continue;
    const label = nameOf(row);
    const pnl = num(row.pnl);
    const amount = pnl === null ? exitLabel(reason) : `${exitLabel(reason)} · ${signedMoney(pnl, 0)}`;
    const title =
      reason === "TRAILING"
        ? copy.toasts.trail(label)
        : reason === "DAILY_HARD_STOP"
          ? copy.toasts.hard
          : reason === "KILL_SWITCH"
            ? copy.toasts.kill(label)
            : reason === "DAY_FLOOR"
              ? copy.toasts.floor(label)
              : copy.toasts.stop(label);
    toast(`reason:${key}`, `${title} · ${amount}`, reason === "TRAILING" ? "ok" : "event");
  }
}

export function LiveProvider({ children }: { children: ReactNode }) {
  useEffect(() => {
    let source: EventSource | null = null;
    let stopped = false;
    let retry = 0;
    let timer = 0;
    const stale = window.setInterval(() => {
      if (state.transport === "sse" && state.updatedAt && Date.now() - state.updatedAt > 10000) commit({ stale: true });
    }, 1000);
    const connect = () => {
      if (stopped) return;
      source = new EventSource("/backend/api/v1/stream");
      source.addEventListener("snapshot", (event) => {
        retry = 0;
        applySnap(JSON.parse((event as MessageEvent).data) as LiveSnap, "snapshot");
      });
      source.addEventListener("diff", (event) => {
        retry = 0;
        applySnap(JSON.parse((event as MessageEvent).data) as LiveSnap, "diff");
      });
      source.addEventListener("heartbeat", () => {
        retry = 0;
        commit({ updatedAt: Date.now(), stale: false, transport: "sse" });
        setTransportMode("sse");
      });
      source.onerror = () => {
        setTransportMode("poll");
        commit({ transport: "poll" });
        source?.close();
        if (stopped) return;
        const delay = [1000, 2000, 5000][Math.min(retry, 2)];
        retry += 1;
        timer = window.setTimeout(connect, delay);
      };
    };
    connect();
    return () => {
      stopped = true;
      window.clearTimeout(timer);
      window.clearInterval(stale);
      source?.close();
    };
  }, []);
  return children;
}
