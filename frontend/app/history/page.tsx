"use client";

import { useEffect, useState } from "react";
import { Shell } from "@/components/Shell";
import { copy } from "@/lib/copy";
import { Num } from "@/components/live-ui";
import { TradeStory, type Story } from "@/components/TradeStory";
import { apiGet, poll } from "@/lib/api";
import { EXIT_REASONS } from "@/lib/exits";
import { money } from "@/lib/pnl";

type Plan = {
  thesis?: string | null;
  invalidation?: string | null;
  direction?: string | null;
  underlying_at_entry?: string | null;
  delta_at_entry?: string | null;
  iv_at_entry?: string | null;
  stop_price?: string | null;
  target_1?: string | null;
  target_2?: string | null;
  planned_risk?: string | null;
  peak_price?: string | null;
};

type Trade = {
  symbol: string;
  underlying: string;
  right?: string | null;
  strike?: string | null;
  expiration?: string | null;
  dte_at_entry?: number | null;
  qty: string;
  qty_open?: string;
  entry_price: string;
  exit_price?: string;
  current_price?: string | null;
  invested: string;
  proceeds?: string;
  market_value?: string | null;
  pnl?: string;
  unrealized_pnl?: string | null;
  realized_partial?: string;
  return_pct?: string | null;
  outcome?: string;
  opened_at?: string | null;
  closed_at?: string | null;
  held_minutes?: number | null;
  entry_fills: number;
  exit_fills: number;
  exit_reason?: string | null;
  exit_detail?: string | null;
  plan?: Plan | null;
  story?: Story | null;
};

type Summary = {
  closed_count: number;
  wins: number;
  losses: number;
  win_rate_pct: string | null;
  invested_closed: string;
  realized_pnl: string;
  realized_return_pct: string | null;
  average_win: string | null;
  average_loss: string | null;
  profit_factor: string | null;
  best: { symbol: string; pnl: string; return_pct: string | null } | null;
  worst: { symbol: string; pnl: string; return_pct: string | null } | null;
  average_hold_minutes: number | null;
  open_count: number;
  open_invested: string;
  unrealized_pnl: string;
  unrealized_complete: boolean;
  total_pnl: string;
  total_return_pct: string | null;
  ai_cost: string | null;
  net_after_ai: string | null;
};

type Period = "today" | "yesterday" | "month" | "range" | "all";

type Report = {
  available: boolean;
  detail?: string;
  window?: { period: Period; start: string | null; end: string | null; includes_today: boolean };
  summary?: Summary;
  closed?: Trade[];
  open?: Trade[];
  orphan_sells?: number;
  paper_limitation?: string;
};

function num(value: string | null | undefined) {
  if (value == null || value === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function count(value: string | null | undefined) {
  const parsed = num(value);
  return parsed === null ? "—" : parsed.toLocaleString("en-US", { maximumFractionDigits: 2 });
}

function dollars(value: string | null | undefined) {
  const parsed = num(value);
  return parsed === null ? "אין נתונים" : money(parsed);
}

function signed(value: string | null | undefined) {
  const parsed = num(value);
  if (parsed === null) return "אין נתונים";
  return parsed > 0 ? `+${money(parsed)}` : money(parsed);
}

function pct(value: string | null | undefined) {
  const parsed = num(value);
  if (parsed === null) return "—";
  return `${parsed > 0 ? "+" : ""}${parsed.toFixed(2)}%`;
}

function tone(value: string | null | undefined) {
  const parsed = num(value);
  if (parsed === null || parsed === 0) return "flat";
  return parsed > 0 ? "up" : "down";
}

function mark(value: number) {
  if (value > 0) return "up";
  if (value < 0) return "down";
  return "";
}

function px(value: string | null | undefined) {
  const parsed = num(value);
  return parsed === null ? "—" : parsed.toFixed(2);
}

function when(iso: string | null | undefined) {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return new Intl.DateTimeFormat("en-GB", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "Asia/Dubai" }).format(date).replace(",", " ·");
}

const WEEKDAYS = ["ראשון", "שני", "שלישי", "רביעי", "חמישי", "שישי", "שבת"];

function tradingDay(iso: string | null | undefined) {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  return new Intl.DateTimeFormat("en-CA", { year: "numeric", month: "2-digit", day: "2-digit", timeZone: "America/New_York" }).format(date);
}

function dayTitle(day: string | null | undefined) {
  if (!day) return "";
  const [year, month, date] = day.split("-").map(Number);
  if (!year || !month || !date) return day;
  const weekday = WEEKDAYS[new Date(Date.UTC(year, month - 1, date)).getUTCDay()] ?? "";
  return `${weekday} ${String(date).padStart(2, "0")}/${String(month).padStart(2, "0")}`;
}

function dayStats(trades: Trade[]) {
  let pnl = 0;
  let invested = 0;
  let wins = 0;
  let losses = 0;
  for (const trade of trades) {
    const result = num(trade.pnl) ?? 0;
    pnl += result;
    invested += num(trade.invested) ?? 0;
    if (result > 0) wins += 1;
    else if (result < 0) losses += 1;
  }
  return { count: trades.length, wins, losses, pnl, pct: invested > 0 ? (pnl / invested) * 100 : null };
}

function DayScore({ trades }: { trades: Trade[] }) {
  const stats = dayStats(trades);
  const percent = stats.pct === null ? "—" : `${stats.pct > 0 ? "+" : ""}${stats.pct.toFixed(1)}%`;
  return (
    <div className="day-score">
      <span>עסקאות<b className="num">{stats.count}</b></span>
      <span>ברווח<b className="num up">{stats.wins}</b></span>
      <span>בהפסד<b className="num down">{stats.losses}</b></span>
      <span>מההשקעה<b className={`num ${mark(stats.pnl)}`}>{percent}</b></span>
    </div>
  );
}

function byDay(trades: Trade[]) {
  const sorted = [...trades].sort((a, b) => new Date(b.closed_at ?? 0).getTime() - new Date(a.closed_at ?? 0).getTime());
  const groups: { day: string; trades: Trade[]; pnl: number }[] = [];
  for (const trade of sorted) {
    const day = tradingDay(trade.closed_at);
    let group = groups[groups.length - 1];
    if (!group || group.day !== day) {
      group = { day, trades: [], pnl: 0 };
      groups.push(group);
    }
    group.trades.push(trade);
    group.pnl += num(trade.pnl) ?? 0;
  }
  return groups;
}

function held(minutes: number | null | undefined) {
  if (minutes == null) return "—";
  if (minutes < 60) return `${minutes} דק׳`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  if (hours < 24) return rest ? `${hours} שע׳ ${rest} דק׳` : `${hours} שע׳`;
  return `${Math.floor(hours / 24)} ימים ${hours % 24} שע׳`;
}

function reason(trade: Trade) {
  if (!trade.exit_reason) return "לא נרשמה";
  return EXIT_REASONS[trade.exit_reason] || trade.exit_reason;
}

function side(trade: Trade) {
  if (trade.right === "PUT") return "PUT · הימור לירידה";
  if (trade.right === "CALL") return "CALL · הימור לעלייה";
  return "—";
}

export default function HistoryPage() {
  const [report, setReport] = useState<Report | null>(null);
  const [failed, setFailed] = useState(false);
  const [open, setOpen] = useState<Trade | null>(null);
  const [choice, setChoice] = useState<string | null>(null);
  const [menu, setMenu] = useState(false);

  useEffect(() => {
    let alive = true;
    const load = () => {
      return apiGet<Report>("/api/v1/report/trades?period=all", 30000)
        .then((body) => {
          if (!alive) return;
          setReport(body);
          setFailed(false);
        })
        .catch(() => alive && setFailed(true));
    };
    const stop = poll(() => load(), 60000);
    return () => {
      alive = false;
      stop();
    };
  }, []);

  useEffect(() => {
    if (!open && !menu) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      if (open) setOpen(null);
      else setMenu(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, menu]);

  const summary = report?.summary;
  const closed = report?.closed ?? [];
  const live = report?.open ?? [];
  const days = byDay(closed);
  const showingAll = choice === "all";
  const active = showingAll ? "" : (choice ?? days[0]?.day ?? "");
  const groups = showingAll ? days : days.filter((group) => group.day === active);
  const selected = showingAll ? null : groups[0];
  const selectedPnl = showingAll ? (num(summary?.realized_pnl) ?? 0) : (selected?.pnl ?? 0);
  const selectedCount = showingAll ? closed.length : (selected?.trades.length ?? 0);
  const selectedLabel = !report ? "טוען" : showingAll ? "כל הימים" : (dayTitle(active) || "תאריך לא ידוע");

  const pick = (next: string) => {
    setChoice(next);
    setMenu(false);
  };

  return (
    <Shell>
      <section className="engine book">
        <header className="ops-head">
          <div>
            <h1>{copy.history.title}</h1>
            <p>{copy.history.subtitle}</p>
          </div>
          <span className={`ops-pip ${failed || report?.available === false ? "warn" : report ? "on" : "warn"}`}>
            <i />
            {failed || report?.available === false ? "אין נתונים" : report ? "Alpaca Paper" : "טוען"}
          </span>
        </header>

        {days.length > 1 ? (
          <div className="day-bars" aria-hidden="true">
            {[...days].reverse().slice(-24).map((group) => {
              const max = Math.max(...days.map((day) => Math.abs(day.pnl)), 1);
              return <i key={group.day || "unknown"} className={group.pnl < 0 ? "down" : ""} style={{ height: `${Math.max(8, (Math.abs(group.pnl) / max) * 72)}px` }} />;
            })}
          </div>
        ) : null}

        <div className="book-pick">
          <button type="button" className="book-trigger" aria-expanded={menu} aria-haspopup="listbox" onClick={() => setMenu((value) => !value)}>
            <span>
              <small>יום מסחר</small>
              {selectedLabel}
            </span>
            <Num className={mark(selectedPnl)}>{report ? signed(String(selectedPnl)) : "—"}</Num>
          </button>
          {menu ? (
            <div className="book-menu" role="listbox" aria-label="ימי מסחר">
              <button type="button" className={`book-option${showingAll ? " on" : ""}`} onClick={() => pick("all")}>
                <span>כל הימים<small>{closed.length} עסקאות</small></span>
                <b className={`num ${tone(summary?.realized_pnl)}`}>{signed(summary?.realized_pnl)}</b>
              </button>
              {days.map((group) => {
                const wins = group.trades.filter((trade) => (num(trade.pnl) ?? 0) > 0).length;
                return (
                  <button key={group.day || "unknown"} type="button" className={`book-option${active === group.day ? " on" : ""}`} onClick={() => pick(group.day)}>
                    <span>{dayTitle(group.day) || "תאריך לא ידוע"}<small>{wins} מתוך {group.trades.length} ברווח</small></span>
                    <b className={`num ${mark(group.pnl)}`}>{signed(String(group.pnl))}</b>
                  </button>
                );
              })}
            </div>
          ) : null}
        </div>
        {menu ? <button type="button" className="book-shade" aria-label="סגור תאריכים" onClick={() => setMenu(false)} /> : null}

        {live.length > 0 ? (
          <section className="desk-days">
            <header className="ops-pane-head">
              <h2>פתוח עכשיו</h2>
              <span>{live.length}</span>
            </header>
            <div className="desk-blotter">
              {live.map((trade) => (
                <button key={`${trade.symbol}-${trade.opened_at}`} type="button" className="desk-pos" onClick={() => setOpen(trade)}>
                  <b>{trade.underlying || "—"} <small>{trade.right}</small></b>
                  <span>{px(trade.entry_price)} → {px(trade.current_price)} · פתוחה</span>
                  <strong className={`num ${tone(trade.unrealized_pnl)}`}>{signed(trade.unrealized_pnl)}<small>{pct(trade.return_pct)}</small></strong>
                </button>
              ))}
            </div>
          </section>
        ) : null}

        <section className="desk-days">
          <header className="ops-pane-head">
            <h2>{selectedLabel}</h2>
          </header>
          {report && selectedCount > 0 && !showingAll ? <DayScore trades={selected?.trades ?? []} /> : null}
          <div className="desk-blotter">
            {groups.length === 0 ? <p className="desk-empty">{report ? "אין עסקה שנסגרה ביום הזה" : "טוען"}</p> : groups.map((group) => (
              <div key={group.day || "unknown"}>
                {showingAll ? (
                  <div className="book-day">
                    <b>{dayTitle(group.day) || "תאריך לא ידוע"}</b>
                    <span className={`num ${mark(group.pnl)}`}>{signed(String(group.pnl))}</span>
                    <DayScore trades={group.trades} />
                  </div>
                ) : null}
                {group.trades.map((trade) => (
                  <button key={`${trade.symbol}-${trade.closed_at}`} type="button" className="desk-pos" onClick={() => setOpen(trade)}>
                    <b>{trade.underlying || "—"} <small>{trade.right}</small></b>
                    <span>{px(trade.entry_price)} → {px(trade.exit_price)} · {reason(trade)}</span>
                    <strong className={`num ${tone(trade.pnl)}`}>{signed(trade.pnl)}<small>{pct(trade.return_pct)}</small></strong>
                  </button>
                ))}
              </div>
            ))}
          </div>
        </section>

        {summary ? (
          <details className="desk-system">
            <summary>סיכום כל הספר</summary>
            <div className="book-facts">
              <span>נסגרו<small className="num">{summary.closed_count}</small></span>
              <span>אחוז הצלחה<small className="num">{summary.win_rate_pct == null ? "—" : `${Number(summary.win_rate_pct).toFixed(0)}%`}</small></span>
              <span>רווח ממוצע<small className="num up">{signed(summary.average_win)}</small></span>
              <span>הפסד ממוצע<small className="num down">{signed(summary.average_loss)}</small></span>
              <span>יחס רווח להפסד<small className="num">{summary.profit_factor ?? "—"}</small></span>
              <span>זמן החזקה<small className="num">{held(summary.average_hold_minutes)}</small></span>
            </div>
          </details>
        ) : null}

        {failed && !report ? <p className="ops-line">אין נתונים. השרת לא החזיר את דוח העסקאות.</p> : null}
        {report?.available === false ? <p className="ops-line">אין חיבור ל-Alpaca Paper ({report.detail}).</p> : null}
        {report?.orphan_sells ? <p className="ops-line">{report.orphan_sells} מילויי מכירה בלי קנייה תואמת בהיסטוריה לא נכללו בחישוב.</p> : null}
        {report?.paper_limitation ? <p className="ops-line">{report.paper_limitation}</p> : null}
      </section>


      {open ? <TradeModal trade={open} onClose={() => setOpen(null)} /> : null}
    </Shell>
  );
}

function TradeModal({ trade, onClose }: { trade: Trade; onClose: () => void }) {
  const isOpen = trade.closed_at == null;
  const result = isOpen ? trade.unrealized_pnl : trade.pnl;
  const plan = trade.plan;
  const lines: [string, string][] = [
    ["חוזה", trade.symbol],
    ["כיוון", side(trade)],
    ["סטרייק · פקיעה", `${trade.strike ?? "—"} · ${trade.expiration ?? "—"}${trade.dte_at_entry != null ? ` (${trade.dte_at_entry} ימים לפקיעה בכניסה)` : ""}`],
    ["כמות", `${count(trade.qty)} חוזים (${Number(trade.qty) * 100} מניות)`],
    ["נפתחה", when(trade.opened_at)],
    ["נסגרה", isOpen ? "עדיין פתוחה" : when(trade.closed_at)],
    ["זמן החזקה", held(trade.held_minutes)],
    ["מחיר כניסה ממוצע", `$${trade.entry_price}`],
    [isOpen ? "מחיר עכשיו" : "מחיר יציאה ממוצע", isOpen ? (trade.current_price ? `$${trade.current_price}` : "—") : `$${trade.exit_price}`],
    ["הושקע", dollars(trade.invested)],
    [isOpen ? "שווי עכשיו" : "התקבל", dollars(isOpen ? trade.market_value : trade.proceeds)],
    [isOpen ? "רווח/הפסד פתוח" : "רווח/הפסד", `${signed(result)} (${pct(trade.return_pct)})`],
    ["מילויים", `${trade.entry_fills} כניסה · ${trade.exit_fills} יציאה`],
  ];
  if (!isOpen) lines.push(["סיבת יציאה", trade.exit_detail ? `${reason(trade)} · ${trade.exit_detail}` : reason(trade)]);
  const planLines: [string, string | null | undefined][] = plan
    ? [
        ["כיוון המניה הצפוי", plan.direction],
        ["מחיר המניה בכניסה", plan.underlying_at_entry],
        ["דלתא בכניסה", plan.delta_at_entry],
        ["IV בכניסה", plan.iv_at_entry],
        ["סטופ מתוכנן", plan.stop_price],
        ["יעד 1", plan.target_1],
        ["יעד 2", plan.target_2],
        ["סיכון מתוכנן ($)", plan.planned_risk],
        ["מחיר שיא בזמן ההחזקה", plan.peak_price],
      ]
    : [];
  return (
    <div className="engine-backdrop" onClick={onClose}>
      <article className="engine-modal" role="dialog" aria-modal="true" aria-labelledby="trade-title" onClick={(event) => event.stopPropagation()}>
        <header>
          <div>
            <p>{isOpen ? "פוזיציה פתוחה" : "עסקה סגורה"} · PAPER</p>
            <h2 id="trade-title" className={tone(result)}>
              {trade.underlying} {trade.right} {trade.strike} · {signed(result)} ({pct(trade.return_pct)})
            </h2>
          </div>
          <button type="button" onClick={onClose}>סגור</button>
        </header>
        <TradeStory story={trade.story} />
        <h3>פרטים טכניים</h3>
        <pre>{lines.map(([label, value]) => `${label}: ${value}`).join("\n")}</pre>
        <h3>התוכנית בכניסה</h3>
        {plan ? (
          <>
            {plan.thesis ? <p>{plan.thesis}</p> : null}
            {plan.invalidation ? <p>מבטל את התזה: {plan.invalidation}</p> : null}
            <pre>{planLines.filter(([, value]) => value).map(([label, value]) => `${label}: ${value}`).join("\n") || "אין נתונים"}</pre>
          </>
        ) : (
          <p>לא נמצאה רשומת תוכנית לעסקה הזו במערכת.</p>
        )}
      </article>
    </div>
  );
}
