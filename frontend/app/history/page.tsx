"use client";

import { useEffect, useState } from "react";
import { Shell } from "@/components/Shell";
import { apiGet } from "@/lib/api";
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

const EXIT_REASONS: Record<string, string> = {
  SESSION_CLOSE: "סגירת יום מסחר",
  STOP: "סטופ הפסד",
  PROTECTED_STOP: "סטופ מוגן",
  TRAILING: "סטופ נגרר",
  TIME_STOP: "זמן ההחזקה הסתיים",
  EXPIRATION: "לפני פקיעה",
  INVALIDATION: "התזה בוטלה",
  LIQUIDITY: "נזילות ירדה",
  KILL_SWITCH: "עצירת חירום",
  CLOSE_ALL: "סגירת כל הפוזיציות",
};

function num(value: string | null | undefined) {
  if (value == null || value === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
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

function when(iso: string | null | undefined) {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return new Intl.DateTimeFormat("en-GB", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit", timeZone: "Asia/Dubai" }).format(date);
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

const PERIODS: { id: Period; label: string }[] = [
  { id: "today", label: "היום" },
  { id: "yesterday", label: "אתמול" },
  { id: "month", label: "מתחילת החודש" },
  { id: "range", label: "טווח תאריכים" },
  { id: "all", label: "מההתחלה" },
];

function span(report: Report | null) {
  const frame = report?.window;
  if (!frame || frame.period === "all") return "כל העסקאות";
  if (!frame.start && !frame.end) return "בחר תאריכים";
  if (frame.start === frame.end) return frame.start ?? "";
  return `${frame.start ?? "…"} עד ${frame.end ?? "…"}`;
}

export default function HistoryPage() {
  const [report, setReport] = useState<Report | null>(null);
  const [failed, setFailed] = useState(false);
  const [open, setOpen] = useState<Trade | null>(null);
  const [period, setPeriod] = useState<Period>("today");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [view, setView] = useState<"all" | "open" | "closed">("all");

  useEffect(() => {
    let alive = true;
    const query = new URLSearchParams({ period });
    if (period === "range") {
      if (start) query.set("start", start);
      if (end) query.set("end", end);
    }
    const load = () => {
      apiGet<Report>(`/api/v1/report/trades?${query.toString()}`)
        .then((body) => {
          if (!alive) return;
          setReport(body);
          setFailed(false);
        })
        .catch(() => alive && setFailed(true));
    };
    setReport(null);
    load();
    const timer = window.setInterval(load, 30000);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, [period, start, end]);

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  const summary = report?.summary;
  const closed = report?.closed ?? [];
  const live = report?.open ?? [];

  const cubes: { label: string; value: string; className?: string }[][] = summary
    ? [
        [
          { label: "רווח/הפסד כולל", value: signed(summary.total_pnl), className: tone(summary.total_pnl) },
          { label: "תשואה על הכסף שהושקע", value: pct(summary.total_return_pct), className: tone(summary.total_return_pct) },
          { label: "עלות AI כוללת", value: dollars(summary.ai_cost) },
          { label: "נטו אחרי עלות AI", value: signed(summary.net_after_ai), className: tone(summary.net_after_ai) },
        ],
        [
          { label: "עסקאות שנסגרו", value: `${summary.closed_count} · ${summary.wins} רווח / ${summary.losses} הפסד` },
          { label: "הושקע בעסקאות שנסגרו", value: dollars(summary.invested_closed) },
          { label: "רווח/הפסד ממומש", value: signed(summary.realized_pnl), className: tone(summary.realized_pnl) },
          { label: "תשואה ממומשת", value: pct(summary.realized_return_pct), className: tone(summary.realized_return_pct) },
        ],
        [
          { label: "אחוז הצלחה", value: summary.win_rate_pct == null ? "—" : `${Number(summary.win_rate_pct).toFixed(0)}%` },
          { label: "רווח ממוצע", value: signed(summary.average_win), className: "up" },
          { label: "הפסד ממוצע", value: signed(summary.average_loss), className: "down" },
          { label: "יחס רווחים להפסדים", value: summary.profit_factor ?? "—" },
        ],
        [
          { label: "פוזיציות פתוחות", value: String(summary.open_count) },
          { label: "הושקע בפתוחות", value: dollars(summary.open_invested) },
          { label: "רווח/הפסד פתוח", value: signed(summary.unrealized_pnl), className: tone(summary.unrealized_pnl) },
          { label: "זמן החזקה ממוצע", value: held(summary.average_hold_minutes) },
        ],
      ]
    : [];

  return (
    <Shell>
      <section className="engine">
        <header className="ops-head">
          <div>
            <h1>היסטוריית עסקאות</h1>
            <p>דוח רווח והפסד לכל עסקה · Alpaca Paper</p>
          </div>
          <span className={`ops-pip ${failed || report?.available === false ? "warn" : report ? "on" : "warn"}`}>
            <i />
            {failed || report?.available === false ? "אין נתונים" : report ? "Alpaca Paper" : "טוען"}
          </span>
        </header>

        <div className="ops-pips" role="tablist" aria-label="תקופה" style={{ flexWrap: "wrap", alignItems: "center", gap: 8 }}>
          {PERIODS.map((item) => (
            <button
              key={item.id}
              type="button"
              role="tab"
              aria-selected={period === item.id}
              className={`ops-pip ${period === item.id ? "on" : ""}`}
              onClick={() => setPeriod(item.id)}
              style={{ cursor: "pointer" }}
            >
              {item.label}
            </button>
          ))}
          {period === "range" ? (
            <>
              <label className="ops-pip">
                מ־ <input type="date" value={start} onChange={(event) => setStart(event.target.value)} className="num" style={{ background: "transparent", color: "inherit", border: 0 }} />
              </label>
              <label className="ops-pip">
                עד <input type="date" value={end} onChange={(event) => setEnd(event.target.value)} className="num" style={{ background: "transparent", color: "inherit", border: 0 }} />
              </label>
            </>
          ) : null}
          <span className="ops-pip num">{span(report)}</span>
        </div>

        <div className="ops-pips" role="tablist" aria-label="סוג עסקה" style={{ flexWrap: "wrap", gap: 8 }}>
          {([
            ["all", `הכול · ${live.length + closed.length}`],
            ["open", `פתוחות · ${live.length}`],
            ["closed", `סגורות · ${closed.length}`],
          ] as const).map(([id, label]) => (
            <button
              key={id}
              type="button"
              role="tab"
              aria-selected={view === id}
              className={`ops-pip ${view === id ? "on" : ""}`}
              onClick={() => setView(id)}
              style={{ cursor: "pointer" }}
            >
              {label}
            </button>
          ))}
        </div>

        {summary ? (
          <div className="engine-board" aria-label="סיכום">
            {cubes.map((row, rowIndex) => (
              <div className="engine-row fit" key={row[0].label}>
                {row.map((cube, index) => (
                  <div key={cube.label} className="engine-cube stat" style={{ animationDelay: `${(rowIndex * 4 + index) * 30}ms` }}>
                    <span>{cube.label}</span>
                    <b className={`num ${cube.className ?? ""}`}>{cube.value}</b>
                  </div>
                ))}
              </div>
            ))}
          </div>
        ) : null}

        {summary?.best && summary?.worst ? (
          <p className="ops-line">
            העסקה הטובה ביותר: <b className="num up">{summary.best.symbol}</b> {signed(summary.best.pnl)} ({pct(summary.best.return_pct)}) · העסקה הגרועה ביותר:{" "}
            <b className="num down">{summary.worst.symbol}</b> {signed(summary.worst.pnl)} ({pct(summary.worst.return_pct)})
          </p>
        ) : null}

        {view !== "closed" ? (
        <section className="ops-pane">
          <h2>פוזיציות פתוחות · {live.length}</h2>
          <div className="ops-scroll">
            <table className="ops-table">
              <thead>
                <tr>
                  <th>נפתחה</th>
                  <th>נכס</th>
                  <th>סוג</th>
                  <th>סטרייק</th>
                  <th>פקיעה</th>
                  <th>כמות</th>
                  <th>כניסה</th>
                  <th>עכשיו</th>
                  <th>הושקע</th>
                  <th>שווי</th>
                  <th>רווח/הפסד</th>
                  <th>%</th>
                  <th>מוחזקת</th>
                </tr>
              </thead>
              <tbody>
                {live.length > 0 ? live.map((trade) => (
                  <tr key={`${trade.symbol}-${trade.opened_at}`} onClick={() => setOpen(trade)} style={{ cursor: "pointer" }}>
                    <td className="num">{when(trade.opened_at)}</td>
                    <td className="num">{trade.underlying}</td>
                    <td className="num">{trade.right ?? "—"}</td>
                    <td className="num">{trade.strike ?? "—"}</td>
                    <td className="num">{trade.expiration ?? "—"}</td>
                    <td className="num">{trade.qty_open ?? trade.qty}</td>
                    <td className="num">${trade.entry_price}</td>
                    <td className="num">{trade.current_price ? `$${trade.current_price}` : "—"}</td>
                    <td className="num">{dollars(trade.invested)}</td>
                    <td className="num">{dollars(trade.market_value)}</td>
                    <td className={`num ${tone(trade.unrealized_pnl)}`}>{signed(trade.unrealized_pnl)}</td>
                    <td className={`num ${tone(trade.return_pct)}`}>{pct(trade.return_pct)}</td>
                    <td className="num">{held(trade.held_minutes)}</td>
                  </tr>
                )) : (
                  <tr><td colSpan={13}>{!report ? "טוען" : report.window?.includes_today === false ? "פוזיציות פתוחות מוצגות רק בתקופה שכוללת את היום" : "אין פוזיציה פתוחה"}</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </section>
        ) : null}

        {view !== "open" ? (
        <section className="ops-pane">
          <h2>עסקאות שנסגרו · {closed.length}</h2>
          <div className="ops-scroll">
            <table className="ops-table">
              <thead>
                <tr>
                  <th>נסגרה</th>
                  <th>נכס</th>
                  <th>סוג</th>
                  <th>סטרייק</th>
                  <th>כמות</th>
                  <th>כניסה</th>
                  <th>יציאה</th>
                  <th>הושקע</th>
                  <th>התקבל</th>
                  <th>רווח/הפסד</th>
                  <th>%</th>
                  <th>מוחזקת</th>
                  <th>סיבת יציאה</th>
                </tr>
              </thead>
              <tbody>
                {closed.length > 0 ? closed.map((trade) => (
                  <tr key={`${trade.symbol}-${trade.closed_at}`} onClick={() => setOpen(trade)} style={{ cursor: "pointer" }}>
                    <td className="num">{when(trade.closed_at)}</td>
                    <td className="num">{trade.underlying}</td>
                    <td className="num">{trade.right ?? "—"}</td>
                    <td className="num">{trade.strike ?? "—"}</td>
                    <td className="num">{trade.qty}</td>
                    <td className="num">${trade.entry_price}</td>
                    <td className="num">${trade.exit_price}</td>
                    <td className="num">{dollars(trade.invested)}</td>
                    <td className="num">{dollars(trade.proceeds)}</td>
                    <td className={`num ${tone(trade.pnl)}`}>{signed(trade.pnl)}</td>
                    <td className={`num ${tone(trade.return_pct)}`}>{pct(trade.return_pct)}</td>
                    <td className="num">{held(trade.held_minutes)}</td>
                    <td>{reason(trade)}</td>
                  </tr>
                )) : (
                  <tr><td colSpan={13}>{report ? "אין עסקה שנסגרה בתקופה הזו" : "טוען"}</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </section>
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
    ["כמות", `${trade.qty} חוזים (${Number(trade.qty) * 100} מניות)`],
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
        <h3>העסקה</h3>
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
