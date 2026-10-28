"use client";

import { useEffect, useState } from "react";
import { Shell } from "@/components/Shell";
import { StaleNote } from "@/components/live-ui";
import { apiGet, holdScreen, poll } from "@/lib/api";

type Summary = { enough?: boolean; note?: string | null; count?: number; net?: string | null; gross?: string | null; fees_note?: string | null; win_rate?: string | null; win_rate_pct?: string | null; profit_factor?: string | null; average_win?: string | null; average_loss?: string | null };

type Finance = {
  available?: boolean;
  stale?: boolean;
  as_of?: string | null;
  account: { equity?: string | null; cash?: string | null; buying_power?: string | null; portfolio_value?: string | null } | null;
  today_pnl: string | null;
  paper_limitation?: string;
  open_positions: number | null;
  exposure: string | null;
  history: { points?: { timestamp: number; equity: number | null; profit_loss: number | null }[] } | null;
  drawdown: { enough?: boolean; note?: string; current?: string; maximum?: string; maximum_percent?: string };
  periods?: { yesterday?: string | null; week?: string | null; month?: string | null; quarter?: string | null; year?: string | null; cumulative?: string | null };
  daily?: { date: string; start: string | null; end: string | null; pnl: string | null }[];
  monthly?: { month: string; pnl: string | null }[];
  utilization?: string | null;
  hours?: { note?: string };
  exits?: { note?: string };
  trades: Summary;
  by_side: Record<string, Summary>;
  by_dte: Record<string, Summary>;
  by_symbol: Record<string, Summary>;
  sector: { note: string };
  best_worst: { note?: string | null };
};

type Risk = { max_positions?: number | null };
type Choice = { id: string; label: string };

const WINDOWS: Choice[] = [
  { id: "day", label: "היום" },
  { id: "week", label: "השבוע" },
  { id: "month", label: "החודש" },
  { id: "quarter", label: "רבעון" },
  { id: "half", label: "6 חודשים" },
  { id: "year", label: "השנה" },
  { id: "all", label: "הכול" },
];

function money(value: string | null | undefined) {
  if (value === null || value === undefined || value === "") return "אין נתונים";
  const number = Number(value);
  if (!Number.isFinite(number)) return "אין נתונים";
  return number.toLocaleString("en-US", { style: "currency", currency: "USD" });
}

function ink(value: string | null | undefined) {
  const number = Number(value);
  if (!Number.isFinite(number) || number === 0) return "";
  return number > 0 ? "up" : "down";
}

function percent(value: string | null | undefined) {
  if (value == null || value === "") return "אין נתונים";
  const number = Number(value);
  if (!Number.isFinite(number)) return "אין נתונים";
  return `${(number * 100).toFixed(2)}%`;
}

export default function FinancePage() {
  const [windowName, setWindowName] = useState("month");
  const [side, setSide] = useState("");
  const [outcome, setOutcome] = useState("");
  const [symbol, setSymbol] = useState("");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [dte, setDte] = useState("");
  const [menu, setMenu] = useState<string | null>(null);
  const [body, setBody] = useState<Finance | null>(null);
  const [maxPositions, setMaxPositions] = useState<number | null>(null);
  const [failed, setFailed] = useState(false);
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    let alive = true;
    const load = () => {
      const query = new URLSearchParams({ window: windowName, side, outcome, symbol, dte, start, end, exit_reason: "", hour: "" });
      return Promise.allSettled([
        apiGet<Finance>(`/api/v1/finance?${query.toString()}`),
        apiGet<Risk>("/api/v1/risk"),
      ]).then(([financeResult, riskResult]) => {
        if (!alive) return;
        if (financeResult.status === "fulfilled") {
          setBody((current) => holdScreen(current, financeResult.value) ?? financeResult.value);
          setFailed(false);
        } else {
          setFailed(true);
          setBody((current) => (current ? { ...current, stale: true, as_of: current.as_of ?? new Date().toISOString() } : current));
        }
        setMaxPositions(riskResult.status === "fulfilled" ? riskResult.value.max_positions ?? null : null);
      });
    };
    const stop = poll(() => load(), 60000);
    return () => {
      alive = false;
      stop();
    };
  }, [windowName, side, outcome, symbol, dte, start, end]);

  useEffect(() => {
    if (!body?.stale) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [body?.stale]);

  useEffect(() => {
    if (!menu) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setMenu(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [menu]);

  const account = body?.account;
  const periods = body?.periods;
  const positions = body?.open_positions;
  const headline = windowName === "day" ? body?.today_pnl
    : windowName === "week" ? periods?.week
      : windowName === "month" ? periods?.month
        : windowName === "quarter" ? periods?.quarter
          : windowName === "year" || windowName === "half" ? periods?.year
            : periods?.cumulative;
  const periodLabel = WINDOWS.find((item) => item.id === windowName)?.label || "החודש";
  const splits = [
    ["CALL", body?.by_side.CALL],
    ["PUT", body?.by_side.PUT],
    ["0DTE", body?.by_dte["0DTE"]],
    ["1DTE", body?.by_dte["1DTE"]],
    ["2DTE+", body?.by_dte["2DTE+"]],
  ] as const;

  return (
    <Shell>
      <section className="engine book" onClick={() => setMenu(null)}>
        <header className="finance-hero">
          <p>{failed && !body ? "אין נתונים" : periodLabel} <StaleNote stale={body?.stale} asOf={body?.as_of} now={now} /></p>
          <p className={`num ${ink(headline)}`}>{body ? money(headline) : failed ? "אין נתונים" : "טוען"}</p>
        </header>
        <div className="period-row" onClick={(event) => event.stopPropagation()}>
          {WINDOWS.filter((item) => ["day", "week", "month", "all"].includes(item.id)).map((item) => (
            <button key={item.id} type="button" className={windowName === item.id ? "choice on" : "choice"} onClick={() => setWindowName(item.id)}>
              <span>{item.label}</span>
            </button>
          ))}
        </div>
        <div className="plain-list">
          <p className="desk-empty">ספר העסקאות הסגורות</p>
          <div className="plain-row"><b>עסקאות סגורות</b><strong className="num">{body?.trades.enough ? String(body.trades.count ?? 0) : body ? "אין נתונים" : "טוען"}</strong></div>
          <div className="plain-row"><b>רווח והפסד</b><strong className={`num ${ink(body?.trades.net)}`}>{body?.trades.enough ? money(body.trades.net) : body ? "אין נתונים" : "טוען"}</strong></div>
          <div className="plain-row"><b>אחוז הצלחה</b><strong className="num">{body?.trades.enough ? percent(body.trades.win_rate) : body ? "אין נתונים" : "טוען"}</strong></div>
          <div className="plain-row"><b>יחס רווח להפסד</b><strong className="num">{body?.trades.enough && body.trades.profit_factor ? body.trades.profit_factor : body ? "אין נתונים" : "טוען"}</strong></div>
        </div>
        <article className="curve-card">
          <Curve points={body?.history?.points || []} />
          <b>עקומת הון · {periodLabel}</b>
        </article>
        <div className="plain-list">
          {[...(body?.daily || [])].sort((a, b) => (a.date < b.date ? 1 : -1)).map((row) => (
            <div className="plain-row" key={row.date}>
              <b>{row.date}</b>
              <strong className={`num ${ink(row.pnl)}`}>{money(row.pnl)}</strong>
            </div>
          ))}
          {[...(body?.monthly || [])].filter((row) => Number(row.pnl)).sort((a, b) => (a.month < b.month ? 1 : -1)).map((row) => (
            <div className="plain-row" key={row.month}>
              <b>{row.month}</b>
              <strong className={`num ${ink(row.pnl)}`}>{money(row.pnl)}</strong>
            </div>
          ))}
          {body && (body.daily || []).length === 0 && (body.monthly || []).length === 0 ? <p className="desk-empty">אין עדיין ימים</p> : null}
        </div>
        <details className="desk-system" onClick={(event) => event.stopPropagation()}>
          <summary>סינון ופירוט</summary>
          <div className="control-filters">
            <Menu name="window" label="טווח" value={windowName} options={WINDOWS} menu={menu} setMenu={setMenu} onChange={setWindowName} />
            <Menu name="side" label="צד" value={side} options={[{ id: "", label: "CALL ו-PUT" }, { id: "CALL", label: "CALL" }, { id: "PUT", label: "PUT" }]} menu={menu} setMenu={setMenu} onChange={setSide} />
            <Menu name="dte" label="DTE" value={dte} options={[{ id: "", label: "כל ה-DTE" }, { id: "0DTE", label: "0DTE" }, { id: "1DTE", label: "1DTE" }, { id: "2DTE+", label: "2DTE+" }]} menu={menu} setMenu={setMenu} onChange={setDte} />
            <Menu name="outcome" label="תוצאה" value={outcome} options={[{ id: "", label: "כל העסקאות" }, { id: "win", label: "רווחיות" }, { id: "loss", label: "מפסידות" }]} menu={menu} setMenu={setMenu} onChange={setOutcome} />
            <label className="menu-field"><span>נכס</span><input placeholder="הכול" value={symbol} onChange={(event) => setSymbol(event.target.value.toUpperCase())} /></label>
            <label className="menu-field"><span>מתאריך</span><input type="date" value={start} onChange={(event) => setStart(event.target.value)} /></label>
            <label className="menu-field"><span>עד תאריך</span><input type="date" value={end} onChange={(event) => setEnd(event.target.value)} /></label>
          </div>
          <div className="plain-list">
            {splits.map(([name, row]) => (
              <div className="plain-row" key={name}>
                <b>{name}</b>
                <strong className={`num ${ink(row?.net)}`}>{sideValue(row)}</strong>
              </div>
            ))}
            {Object.entries(body?.by_symbol || {}).map(([name, row]) => (
              <div className="plain-row" key={name}>
                <b>{name}</b>
                <strong className={`num ${ink(row.net)}`}>{row.enough ? money(row.net) : "אין מספיק נתונים"}</strong>
              </div>
            ))}
            <div className="plain-row"><b>פוזיציות</b><strong className="num">{positions == null ? "אין נתונים" : maxPositions == null ? String(positions) : `${positions} / ${maxPositions}`}</strong></div>
            <div className="plain-row"><b>חשיפה</b><strong className="num">{money(body?.exposure)}</strong></div>
            <div className="plain-row"><b>ניצול</b><strong className="num">{percent(body?.utilization)}</strong></div>
            <div className="plain-row"><b>מזומן</b><strong className="num">{money(account?.cash)}</strong></div>
            <div className="plain-row"><b>משיכת שיא</b><strong className="num">{body?.drawdown.enough ? money(body.drawdown.maximum) : "אין נתונים"}</strong></div>
          </div>
        </details>
      </section>
    </Shell>
  );
}

function sideValue(row?: Summary) {
  return row?.enough ? `${row.count ?? 0} · ${money(row.net)}` : "אין מספיק נתונים";
}

function Menu({ name, label, value, options, menu, setMenu, onChange }: {
  name: string;
  label: string;
  value: string;
  options: Choice[];
  menu: string | null;
  setMenu: (name: string | null) => void;
  onChange: (id: string) => void;
}) {
  const current = options.find((item) => item.id === value)?.label || label;
  const shown = menu === name;
  return (
    <div className="menu">
      <button type="button" className="menu-btn" aria-expanded={shown} onClick={() => setMenu(shown ? null : name)}>
        <span>{label}</span>
        <b>{current}</b>
      </button>
      {shown ? (
        <ul className="menu-list">
          {options.map((item) => (
            <li key={item.id || "all"}>
              <button type="button" className={item.id === value ? "on" : ""} onClick={() => { onChange(item.id); setMenu(null); }}>
                {item.label}
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

function Curve({ points }: { points: { equity: number | null }[] }) {
  const values = points.map((point) => point.equity).filter((value): value is number => value !== null);
  if (values.length < 2) return <p className="text-sm text-[var(--muted)]">אין עדיין היסטוריה מספקת</p>;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const path = values
    .map((value, index) => {
      const x = (index / (values.length - 1)) * 280;
      const y = 64 - ((value - min) / span) * 56;
      return `${index === 0 ? "M" : "L"}${x.toFixed(1)} ${y.toFixed(1)}`;
    })
    .join(" ");
  return (
    <svg viewBox="0 0 280 72" className="mx-auto h-16 w-full max-w-xl" role="img" aria-label="עקומת הון">
      <path d={path} fill="none" stroke="currentColor" strokeWidth="1.5" />
    </svg>
  );
}
