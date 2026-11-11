"use client";

import { useEffect, useState } from "react";
import { Shell } from "@/components/Shell";
import { apiGet, poll } from "@/lib/api";

type Signal = { state?: string; reason?: string; at?: string | null; price?: string | null; vix?: string | null; vix_source?: string | null };
type Leg = { symbol?: string; side?: string; strike?: string | null };
type Position = {
  open?: boolean;
  side?: string;
  qty?: number;
  debit?: string | null;
  value?: string | null;
  pnl?: string | null;
  width?: string | null;
  expiry?: string | null;
  legs?: Leg[];
};
type Status = {
  enabled?: boolean;
  mode?: string;
  paper_only?: boolean;
  account_last4?: string;
  account_match?: boolean;
  stale?: boolean;
  heartbeat_age_seconds?: number;
  signal?: Signal;
  vix_source?: string | null;
  position?: Position;
  day_pnl?: string | null;
  week_pnl?: string | null;
  bankroll?: string | null;
  brakes?: { kill?: boolean; weekly?: boolean; daily?: boolean; entries_today?: number; entries_max?: number; account?: boolean };
};
type Trade = {
  session_date?: string;
  side?: string;
  qty?: number;
  long_symbol?: string;
  short_symbol?: string;
  debit?: string | null;
  exit_credit?: string | null;
  exit_reason?: string | null;
  pnl?: string | null;
  status?: string;
};

function cash(value?: string | null, signed = false) {
  if (value == null || value === "") return "—";
  const number = Number(value);
  if (!Number.isFinite(number)) return "—";
  const body = number.toLocaleString("en-US", { style: "currency", currency: "USD" });
  if (!signed) return body;
  return number > 0 ? `+${body}` : body;
}

function signalText(signal?: Signal) {
  if (!signal || signal.state === "none" || !signal.state) return "אין איתות";
  if (signal.state === "up") return "עלייה · קול";
  if (signal.state === "down") return "ירידה · פוט";
  return `דילוג · ${signal.reason || ""}`.trim();
}

function AviScreen() {
  const [status, setStatus] = useState<Status | null>(null);
  const [trades, setTrades] = useState<Trade[]>([]);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let alive = true;
    const stop = poll(async () => {
      try {
        const [nextStatus, nextTrades] = await Promise.all([
          apiGet<Status>("/api/v1/avi/status"),
          apiGet<{ items?: Trade[] }>("/api/v1/avi/trades"),
        ]);
        if (!alive) return;
        setStatus(nextStatus);
        setTrades(nextTrades.items || []);
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

  const mode = status?.mode || (status?.enabled ? "running" : "disabled");
  const modeLabel = mode === "running" ? "פעיל" : mode === "disabled" ? "כבוי" : mode === "refused" ? "נחסם" : mode === "not_configured" ? "חסר חשבון" : "ממתין";
  const position = status?.position;
  const brakes = status?.brakes;
  const open = Boolean(position?.open);

  return (
    <div className="spy-desk" id="board">
      <header className="spy-head">
        <div>
          <p className="spy-kicker">ארכיון · קריאה בלבד · הוחלף על ידי אופציות 2</p>
          <h1>ארכיון אבי</h1>
        </div>
        <span className={`spy-mode ${mode}`}>{modeLabel}</span>
      </header>
      {failed ? <p className="spy-note">הקריאה האחרונה נכשלה. המסך שומר את מה שכבר נטען.</p> : null}
      {status?.stale ? <p className="spy-note">פעימת המנוע ישנה. בודקים שהשירות avi רץ.</p> : null}
      <section className="spy-stats">
        <article>
          <span>מצב</span>
          <b>{status?.paper_only === false ? "לא נייר" : "נייר"}</b>
        </article>
        <article>
          <span>חשבון</span>
          <b>{status?.account_last4 ? `•••${status.account_last4}` : "—"}</b>
        </article>
        <article>
          <span>רווח היום</span>
          <b className={Number(status?.day_pnl) < 0 ? "down" : "up"}>{cash(status?.day_pnl, true)}</b>
        </article>
        <article>
          <span>רווח השבוע</span>
          <b className={Number(status?.week_pnl) < 0 ? "down" : "up"}>{cash(status?.week_pnl, true)}</b>
        </article>
      </section>
      <section className="spy-symbols">
        <article id="signal">
          <b>איתות היום</b>
          <span>{signalText(status?.signal)}</span>
          <span>QQQ {status?.signal?.price || "—"} · VIX {status?.signal?.vix || "—"} {status?.vix_source || status?.signal?.vix_source || ""}</span>
        </article>
        <article id="position">
          <b>פוזיציה</b>
          <span>{open ? `${position?.side === "down" ? "פוט" : "קול"} ${position?.qty || 0} · רוחב ${position?.width || "—"}` : "שטוח"}</span>
          <span>דביט {position?.debit || "—"} · שווי {position?.value || "—"}</span>
          <strong className={Number(position?.pnl) < 0 ? "down" : "up"}>{open ? cash(position?.pnl, true) : "—"}</strong>
          {(position?.legs || []).map((leg) => (
            <span key={`${leg.side}-${leg.symbol}`}>{leg.side === "short" ? "שורט" : "לונג"} {leg.symbol}</span>
          ))}
        </article>
        <article id="brakes">
          <b>בלמים</b>
          <span>בנק {cash(status?.bankroll)}</span>
          <span>עסקאות {brakes?.entries_today ?? 0}/{brakes?.entries_max ?? 1}</span>
          <span>{brakes?.kill ? "Kill פעיל" : brakes?.weekly ? "בלם שבועי" : brakes?.account ? "חשבון לא תואם" : "פתוח"}</span>
          <span>{status?.account_match === false ? "הפין לא תואם" : "פין תואם"}</span>
        </article>
      </section>
      <section className="spy-panel" id="trades">
        <h2>עסקאות</h2>
        {trades.length ? (
          <div className="spy-table-wrap">
            <table className="ops-cardify">
              <thead>
                <tr>
                  <th>יום</th>
                  <th>כיוון</th>
                  <th>כמות</th>
                  <th>רגליים</th>
                  <th>דביט</th>
                  <th>יציאה</th>
                  <th>סיבה</th>
                  <th>רווח</th>
                </tr>
              </thead>
              <tbody>
                {trades.slice(0, 30).map((trade, index) => (
                  <tr key={`${trade.session_date}-${index}`}>
                    <td>{trade.session_date}</td>
                    <td>{trade.side === "down" ? "ירידה" : "עלייה"}</td>
                    <td>{trade.qty}</td>
                    <td>{trade.long_symbol} / {trade.short_symbol}</td>
                    <td>{trade.debit || "—"}</td>
                    <td>{trade.exit_credit || "—"}</td>
                    <td>{trade.exit_reason || trade.status}</td>
                    <td className={Number(trade.pnl) < 0 ? "down" : "up"}>{cash(trade.pnl, true)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="spy-empty">עדיין אין עסקאות.</p>
        )}
      </section>
    </div>
  );
}

export default function AviPage() {
  return (
    <Shell>
      <AviScreen />
    </Shell>
  );
}
