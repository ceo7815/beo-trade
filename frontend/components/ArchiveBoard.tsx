"use client";

import { useEffect, useState } from "react";
import { apiGet, apiPost } from "@/lib/api";
import { DailyBars, EquityCurve, StrategyBars } from "@/components/charts";

type Summary = {
  ready?: boolean;
  detail?: string;
  account?: { equity?: string | null; cash?: string | null; last_equity?: string | null; last4?: string | null; account_match?: boolean } | null;
  counts?: { orders?: number; activities?: number; days?: number; positions?: number; local_rows?: number };
  orders_by_status?: Record<string, number>;
  last_run?: { status?: string; finished_at?: string; detail?: string } | null;
};

type Point = { t?: string; equity?: string | null; day_pnl?: string | null };
type Trade = {
  source?: string;
  id?: string;
  symbol?: string;
  side?: string;
  qty?: string | number | null;
  entry_price?: string | number | null;
  exit_price?: string | number | null;
  pnl?: string | number | null;
  reason?: string;
  at?: string | null;
};
type Order = {
  id?: string;
  status?: string;
  symbol?: string;
  submitted_at?: string | null;
  side?: string | null;
  qty?: string | null;
  filled_avg_price?: string | null;
  type?: string | null;
};

function cash(value?: string | number | null, signed = false) {
  if (value == null || value === "") return "—";
  const number = Number(value);
  if (!Number.isFinite(number)) return "—";
  const body = number.toLocaleString("en-US", { style: "currency", currency: "USD" });
  if (!signed) return body;
  return number > 0 ? `+${body}` : body;
}

export function ArchiveBoard() {
  const [summary, setSummary] = useState<Summary | null>(null);
  const [points, setPoints] = useState<Point[]>([]);
  const [trades, setTrades] = useState<Trade[]>([]);
  const [orders, setOrders] = useState<Order[]>([]);
  const [orderTotal, setOrderTotal] = useState(0);
  const [note, setNote] = useState("");
  const [running, setRunning] = useState(false);
  const [failed, setFailed] = useState(false);

  async function load() {
    const [nextSummary, nextEquity, nextTrades, nextOrders] = await Promise.all([
      apiGet<Summary>("/api/v1/archive/paper_100k/summary"),
      apiGet<{ points?: Point[] }>("/api/v1/archive/paper_100k/equity?period=all"),
      apiGet<{ items?: Trade[] }>("/api/v1/archive/paper_100k/trades?limit=500"),
      apiGet<{ items?: Order[]; total?: number }>("/api/v1/archive/paper_100k/orders?limit=100"),
    ]);
    setSummary(nextSummary);
    setPoints(nextEquity.points || []);
    setTrades(nextTrades.items || []);
    setOrders(nextOrders.items || []);
    setOrderTotal(nextOrders.total || 0);
    setFailed(false);
  }

  useEffect(() => {
    let alive = true;
    load().catch(() => alive && setFailed(true));
    return () => {
      alive = false;
    };
  }, []);

  async function run() {
    setRunning(true);
    setNote("");
    try {
      const body = await apiPost<Summary>("/api/v1/archive/paper_100k/run");
      const counts = body.counts;
      setNote(`נשמר. פקודות ${counts?.orders ?? 0}, פעולות ${counts?.activities ?? 0}, ימים ${counts?.days ?? 0}.`);
    } catch (error) {
      setNote(error instanceof Error ? error.message : "השמירה לא הושלמה");
    } finally {
      setRunning(false);
      load().catch(() => setFailed(true));
    }
  }

  const equity = points.map((point) => Number(point.equity)).filter((value) => Number.isFinite(value));
  const curve = equity.map((value, index) => ({ value, label: points[index]?.t || "" }));
  const days = points
    .map((point) => ({ label: (point.t || "").slice(0, 10), value: Number(point.day_pnl) }))
    .filter((row) => Number.isFinite(row.value));
  const bySymbol = new Map<string, number>();
  for (const trade of trades) {
    const pnl = Number(trade.pnl);
    if (!Number.isFinite(pnl)) continue;
    const key = trade.symbol || trade.source || "אחר";
    bySymbol.set(key, (bySymbol.get(key) || 0) + pnl);
  }
  const strategies = [...bySymbol.entries()].map(([label, value]) => ({ label, value }));
  const ready = Boolean(summary?.ready);

  return (
    <section className="archive-board">
      <header className="archive-head">
        <div>
          <p className="spy-kicker">PAPER · חשבון ישן</p>
          <h1>פייפר 100K</h1>
        </div>
        <span className="archive-badge">ארכיון</span>
      </header>
      <p className="archive-note">נקרא מהמסד שלנו. השמירה לא שולחת פקודות.</p>
      <button type="button" className={`archive-save${ready ? " ghost" : ""}`} disabled={running} onClick={run}>
        {running ? "שומר…" : ready ? "רענון ארכיון" : "שמירת ארכיון"}
      </button>
      {failed ? <p className="spy-note">הקריאה לארכיון נכשלה.</p> : null}
      {note ? <p className="archive-note">{note}</p> : null}
      <section className="spy-stats">
        <article>
          <span>הון</span>
          <b>{cash(summary?.account?.equity)}</b>
        </article>
        <article>
          <span>מזומן</span>
          <b>{cash(summary?.account?.cash)}</b>
        </article>
        <article>
          <span>הון אתמול</span>
          <b>{cash(summary?.account?.last_equity)}</b>
        </article>
        <article>
          <span>חשבון</span>
          <b>••••{summary?.account?.last4 || "—"}</b>
        </article>
      </section>
      <section className="archive-counts">
        <span>פקודות {summary?.counts?.orders ?? "—"}</span>
        <span>פעולות {summary?.counts?.activities ?? "—"}</span>
        <span>ימים {summary?.counts?.days ?? "—"}</span>
        <span>פוזיציות {summary?.counts?.positions ?? "—"}</span>
        <span>יומן {summary?.counts?.local_rows ?? "—"}</span>
      </section>
      {!ready ? (
        <div className="archive-empty">
          <b>הארכיון עדיין ריק</b>
          <p>אחרי Deploy Now לוחצים «שמירת ארכיון», בודקים את הספירה, ורק אז מוחקים את החשבון ב-Alpaca.</p>
        </div>
      ) : null}
      <EquityCurve points={curve} label="עקומת הון" />
      <DailyBars rows={days} label="רווח והפסד לפי יום" />
      <StrategyBars rows={strategies} label="לפי סימול ביומן" />
      <section className="ops-pane">
        <header className="ops-pane-head">
          <h2>עסקאות ויומן</h2>
          <span>{trades.length}</span>
        </header>
        {trades.length ? (
          <ul className="archive-list">
            {trades.map((trade) => (
              <li key={`${trade.source}-${trade.id}`}>
                <b>{trade.symbol || trade.source}</b>
                <span>{trade.side || trade.reason || trade.source}</span>
                <span>{trade.at ? String(trade.at).slice(0, 16).replace("T", " ") : "—"}</span>
                <strong className={Number(trade.pnl) < 0 ? "down" : "up"}>{trade.pnl == null || trade.pnl === "" ? cash(trade.entry_price) : cash(trade.pnl, true)}</strong>
              </li>
            ))}
          </ul>
        ) : (
          <p className="chart-empty">אין שורות יומן בארכיון.</p>
        )}
      </section>
      <section className="ops-pane">
        <header className="ops-pane-head">
          <h2>פקודות</h2>
          <span>{orderTotal}</span>
        </header>
        {orders.length ? (
          <ul className="archive-list">
            {orders.map((order) => (
              <li key={order.id}>
                <b>{order.symbol || "—"}</b>
                <span>{order.status}</span>
                <span>{order.side || order.type || "—"}</span>
                <strong>{order.filled_avg_price || order.qty || "—"}</strong>
              </li>
            ))}
          </ul>
        ) : (
          <p className="chart-empty">אין פקודות בארכיון.</p>
        )}
      </section>
    </section>
  );
}
