"use client";

import { useEffect, useState } from "react";
import { Shell } from "@/components/Shell";
import { DailyBars, EquityCurve, StrategyBars } from "@/components/charts";
import { apiGet, poll } from "@/lib/api";

type Point = { t: string; price: string | null; upper: string | null; lower: string | null; vwap: string | null };
type Marker = { t: string; kind: string; price: string | null; reason?: string };
type Shadow = { symbol: string; side?: string | null; qty?: number; entry_price?: string | null; day_pnl?: string | null };
type SymbolCard = {
  symbol: string;
  price?: string | null;
  upper?: string | null;
  lower?: string | null;
  vwap?: string | null;
  pnl?: string | null;
  position?: { side?: string; qty?: number };
};
type Status = {
  enabled?: boolean;
  mode?: string;
  sample?: boolean;
  account_last4?: string;
  account_match?: boolean;
  stale?: boolean;
  position?: { side?: string; qty?: number; entry_price?: string | null };
  day_pnl?: string | null;
  equity?: string | null;
  day_trades_left?: number | null;
  daytrade_count?: number | null;
  pdt_guard?: boolean;
  brakes?: { daily_loss?: boolean; drawdown?: boolean; kill?: boolean; entries_today?: number; entries_max?: number; halt_reason?: string; pdt_blocked?: boolean };
  symbols?: SymbolCard[];
  shadow?: Shadow[];
  series?: Point[];
  markers?: Marker[];
};
type Trade = {
  symbol?: string;
  session_date?: string;
  side?: string;
  qty?: number;
  entry_price?: string | null;
  exit_price?: string | null;
  exit_reason?: string | null;
  pnl?: string | null;
  status?: string;
};
type Day = { session_date?: string; equity_last?: string | null; pnl?: string | null; cumulative_pnl?: string | null };

function cash(value?: string | null, signed = false) {
  if (value == null || value === "") return "—";
  const number = Number(value);
  if (!Number.isFinite(number)) return "—";
  const body = number.toLocaleString("en-US", { style: "currency", currency: "USD" });
  if (!signed) return body;
  return number > 0 ? `+${body}` : body;
}

function ZoneChart({ series, markers }: { series: Point[]; markers: Marker[] }) {
  const values = series.flatMap((row) => [row.price, row.upper, row.lower, row.vwap].map((item) => Number(item)).filter((item) => Number.isFinite(item)));
  if (!values.length) return <p className="spy-empty">אין נתוני דקה להיום. האזור יופיע כשהמנוע שומר נרות.</p>;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const width = 760;
  const height = 280;
  const pad = 18;
  const xAt = (index: number) => pad + (index * (width - pad * 2)) / Math.max(1, series.length - 1);
  const yAt = (value: number) => pad + (1 - (value - min) / span) * (height - pad * 2);
  const path = (key: keyof Point) =>
    series
      .map((row, index) => {
        const value = Number(row[key]);
        return Number.isFinite(value) ? `${xAt(index).toFixed(1)},${yAt(value).toFixed(1)}` : "";
      })
      .filter(Boolean)
      .join(" ");
  return (
    <svg className="spy-chart" viewBox={`0 0 ${width} ${height}`} role="img" aria-label="אזור רעש, מחיר ו-VWAP">
      <polyline points={path("upper")} className="band" />
      <polyline points={path("lower")} className="band" />
      <polyline points={path("vwap")} className="vwap" />
      <polyline points={path("price")} className="price" />
      {markers.map((marker, index) => {
        const at = series.findIndex((row) => row.t === marker.t);
        const price = Number(marker.price);
        if (at < 0 || !Number.isFinite(price)) return null;
        return <circle key={`${marker.t}-${index}`} cx={xAt(at)} cy={yAt(price)} r="4.5" className={marker.kind === "entry" ? "entry" : "exit"} />;
      })}
    </svg>
  );
}

function SpyScreen() {
  const [status, setStatus] = useState<Status | null>(null);
  const [trades, setTrades] = useState<Trade[]>([]);
  const [days, setDays] = useState<Day[]>([]);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let alive = true;
    const stop = poll(async () => {
      try {
        const [nextStatus, nextTrades, nextDays] = await Promise.all([
          apiGet<Status>("/api/v1/spy/status"),
          apiGet<{ items?: Trade[] }>("/api/v1/spy/trades"),
          apiGet<{ items?: Day[] }>("/api/v1/spy/daily"),
        ]);
        if (!alive) return;
        setStatus(nextStatus);
        setTrades(nextTrades.items || []);
        setDays(nextDays.items || []);
        setFailed(false);
      } catch {
        if (alive) setFailed(true);
      }
    }, 15000);
    return () => {
      alive = false;
      stop();
    };
  }, []);

  const brakes = status?.brakes;
  const position = status?.position;
  const left = status?.day_trades_left;
  const mode = status?.mode || (status?.enabled ? "running" : "disabled");
  const sample = status?.sample === true || mode === "sample";
  const shadow = status?.shadow?.[0];
  const modeLabel = sample ? "דוגמה" : mode === "running" ? "פעיל" : mode === "disabled" ? "כבוי" : mode === "refused" ? "נחסם" : "ממתין";

  return (
    <div className="spy-desk" id="board">
      {sample ? <p className="spy-sample">נתוני דוגמה — לא מסחר אמיתי ולא פקודות</p> : null}
      <header className="spy-head">
        <div>
          <p className="spy-kicker">{sample ? "SAMPLE · נתוני דוגמה" : "PAPER · QQQ · חשבון אלי"}</p>
          <h1>מסחר יומי אלי</h1>
        </div>
        <span className={`spy-mode ${sample ? "sample" : mode}`}>{modeLabel}</span>
      </header>
      {failed ? <p className="spy-note">הקריאה האחרונה נכשלה. המסך שומר את מה שכבר נטען.</p> : null}
      {status?.stale ? <p className="spy-note">פעימת המנוע ישנה. בודקים שהשירות spy רץ.</p> : null}
      <section className="spy-stats">
        <article>
          <span>רווח היום</span>
          <b className={Number(status?.day_pnl) < 0 ? "down" : "up"}>{cash(status?.day_pnl, true)}</b>
        </article>
        <article>
          <span>הון</span>
          <b>{cash(status?.equity)}</b>
        </article>
        <article>
          <span>פוזיציה</span>
          <b>{position?.qty ? `${position.side === "short" ? "שורט" : "לונג"} ${position.qty}` : "שטוח"}</b>
        </article>
        <article>
          <span>עסקאות יום שנותרו</span>
          <b>{left == null ? "לא חל" : String(left)}</b>
        </article>
      </section>
      {(status?.symbols || []).length ? (
        <section className="spy-symbols" id="positions">
          {(status?.symbols || []).map((card) => (
            <article key={card.symbol}>
              <b>{card.symbol}</b>
              <span>מחיר {card.price || "—"}</span>
              <span>עליון {card.upper || "—"} · תחתון {card.lower || "—"}</span>
              <span>VWAP {card.vwap || "—"}</span>
              <span>{card.position?.qty ? `${card.position.side === "short" ? "שורט" : "לונג"} ${card.position.qty}` : "שטוח"}</span>
              <strong className={Number(card.pnl) < 0 ? "down" : "up"}>{cash(card.pnl, true)}</strong>
            </article>
          ))}
        </section>
      ) : null}
      <section className="spy-layout">
        <div className="spy-panel">
          <div className="spy-legend">
            <span><i className="price" /> מחיר</span>
            <span><i className="band" /> גבול רעש</span>
            <span><i className="vwap" /> VWAP</span>
            <span><i className="entry" /> כניסה</span>
            <span><i className="exit" /> יציאה</span>
          </div>
          <ZoneChart series={status?.series || []} markers={status?.markers || []} />
        </div>
        {shadow?.symbol ? (
        <aside className="spy-shadow">
          <p>צל {shadow?.symbol || "QQQ"}</p>
          <b>בלי פקודות</b>
          <span>P&L היפותטי</span>
          <strong className={Number(shadow?.day_pnl) < 0 ? "down" : "up"}>{cash(shadow?.day_pnl, true)}</strong>
          <span>{shadow?.qty ? `${shadow.side === "short" ? "שורט" : "לונג"} ${shadow.qty}` : "אין פוזיציית צל"}</span>
        </aside>
        ) : null}
      </section>
      <section className="spy-panel" id="brakes">
        <h2>בלמים</h2>
        <div className="spy-meta">
          <p>חשבון ••••{status?.account_last4 || "—"} {status?.account_match === false ? "· לא תואם" : ""}</p>
          <p>כניסות {brakes?.entries_today ?? 0}/{brakes?.entries_max ?? 4}</p>
          <p>{brakes?.kill ? "Kill פעיל" : brakes?.daily_loss ? "בלם הפסד יומי" : brakes?.drawdown ? "בלם ירידה" : brakes?.pdt_blocked ? "PDT חסם כניסה" : "בלמים פתוחים"}</p>
          <p>{status?.pdt_guard === false ? "שומר PDT כבוי" : `מונה PDT ${status?.daytrade_count ?? "—"}`}</p>
        </div>
      </section>
      <section className="spy-panel" id="trades">
        <h2>עסקאות</h2>
        {trades.length ? (
          <div className="spy-table-wrap">
            <table className="ops-cardify">
              <thead>
                <tr>
                  <th>סימול</th>
                  <th>יום</th>
                  <th>צד</th>
                  <th>כמות</th>
                  <th>כניסה</th>
                  <th>יציאה</th>
                  <th>סיבה</th>
                  <th>רווח</th>
                </tr>
              </thead>
              <tbody>
                {trades.slice(0, 30).map((trade, index) => (
                  <tr key={`${trade.session_date}-${index}`}>
                    <td>{trade.symbol || "SPY"}</td>
                    <td>{trade.session_date}</td>
                    <td>{trade.side === "short" ? "שורט" : "לונג"}</td>
                    <td>{trade.qty}</td>
                    <td>{trade.entry_price || "—"}</td>
                    <td>{trade.exit_price || "—"}</td>
                    <td>{trade.exit_reason || trade.status}</td>
                    <td className={Number(trade.pnl) < 0 ? "down" : "up"}>{cash(trade.pnl, true)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="spy-empty">עדיין אין עסקאות SPY.</p>
        )}
      </section>
      <section className="spy-panel" id="history">
        <h2>הון יומי</h2>
        <EquityCurve points={days.map((day) => ({ label: day.session_date, value: Number(day.cumulative_pnl) })).filter((point) => Number.isFinite(point.value))} label="עקומת רווח מצטבר" />
        <DailyBars rows={days.map((day) => ({ label: day.session_date || "", value: Number(day.pnl) })).filter((row) => Number.isFinite(row.value))} />
        <StrategyBars
          rows={Object.entries(
            trades.reduce<Record<string, number>>((acc, trade) => {
              const pnl = Number(trade.pnl);
              if (!Number.isFinite(pnl)) return acc;
              const key = trade.symbol || "אחר";
              acc[key] = (acc[key] || 0) + pnl;
              return acc;
            }, {}),
          ).map(([label, value]) => ({ label, value }))}
          label="לפי סימול"
        />
      </section>
    </div>
  );
}

export default function SpyPage() {
  return (
    <Shell>
      <SpyScreen />
    </Shell>
  );
}
