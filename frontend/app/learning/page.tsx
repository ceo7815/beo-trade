"use client";

import { useEffect, useState } from "react";
import { Shell } from "@/components/Shell";
import { copy } from "@/lib/copy";
import { apiGet, apiPost, holdScreen, poll } from "@/lib/api";
import { StaleNote } from "@/components/live-ui";
import { exitLabel } from "@/lib/exits";

type Closed = {
  symbol: string;
  underlying: string;
  right: string;
  strike: string;
  qty: string;
  entry_price: string;
  exit_price: string;
  opened_at: string;
  closed_at: string;
  dte_at_entry: number | null;
  exit_reason: string;
  pnl: string | null;
};

type PathStats = {
  covered: boolean;
  marks?: number;
  after_marks?: number;
  peak_price?: string;
  peak_pct?: string;
  peak_minutes?: number;
  trough_price?: string;
  trough_pct?: string;
  trough_minutes?: number;
  giveback?: string;
  peak_source?: "marks" | "position_state";
  after_best_pct?: string;
  after_worst_pct?: string;
  after_last_pct?: string;
};

type Replay = { exit_price: string; reason: string; at: string; pnl: string; complete: boolean };
type LearnedTrade = Closed & { path: PathStats; current_rules: Replay | null };
type Rules = { stop_pct: number; protect_at_pct: number; protective_pct: number; trail_arm_pct: number; trail_pct: number; invalidation_pct: number };
type Backfill = { running: boolean; trades: number; done: number; marks: number; skipped: number; error: string | null; finished_at: string | null };
type TradesBody = { available?: boolean; stale?: boolean; as_of?: string | null; trades?: LearnedTrade[]; covered?: number; backfill?: Backfill; rules?: Rules };
type LabRow = Rules & { pnl: string; wins: number; trades: number; complete: number; current: boolean; rank: number };
type LabBody = { available?: boolean; trades_total?: number; trades_covered?: number; actual_pnl?: string; current?: LabRow | null; best?: LabRow[]; combinations?: number };
type PathPoint = { at: string; bid: string; ask: string | null; underlying: string | null; source: string };

type Group = { name: string; trades: number; wins: number; pnl: number };

const ZONE = "America/New_York";
const WEEKDAYS = ["ראשון", "שני", "שלישי", "רביעי", "חמישי", "שישי", "שבת"];

function nyParts(stamp: string) {
  const parts = new Intl.DateTimeFormat("en-US", { timeZone: ZONE, hour: "2-digit", minute: "2-digit", hour12: false, weekday: "short", year: "numeric", month: "2-digit", day: "2-digit" }).formatToParts(new Date(stamp));
  const get = (type: string) => parts.find((part) => part.type === type)?.value ?? "";
  const hour = Number(get("hour")) % 24;
  return { minutes: hour * 60 + Number(get("minute")), day: `${get("year")}-${get("month")}-${get("day")}`, weekday: ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"].indexOf(get("weekday")) };
}

function nyTime(stamp: string) {
  return new Intl.DateTimeFormat("he-IL", { timeZone: ZONE, day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false }).format(new Date(stamp));
}

function nyClock(stamp: string) {
  return new Intl.DateTimeFormat("he-IL", { timeZone: ZONE, hour: "2-digit", minute: "2-digit", hour12: false }).format(new Date(stamp));
}

function dteGroup(dte: number | null) {
  if (dte == null) return "לא ידוע";
  if (dte <= 0) return "0 ימים";
  if (dte <= 2) return "1–2 ימים";
  if (dte <= 5) return "3–5 ימים";
  return "6 ימים ומעלה";
}

function hourGroup(minutes: number) {
  if (minutes < 10 * 60) return "לפני 10:00";
  if (minutes < 11 * 60) return "10:00–11:00";
  if (minutes < 13 * 60) return "11:00–13:00";
  if (minutes < 15 * 60) return "13:00–15:00";
  return "אחרי 15:00";
}

function dayName(stamp: string) {
  const parts = nyParts(stamp);
  const [year, month, date] = parts.day.split("-").map(Number);
  const weekday = WEEKDAYS[parts.weekday] ?? "";
  if (!year || !month || !date) return parts.day || "לא ידוע";
  return `${weekday} ${String(date).padStart(2, "0")}/${String(month).padStart(2, "0")}`;
}

function groupBy(rows: Closed[], key: (row: Closed) => string): Group[] {
  const book = new Map<string, Group>();
  for (const row of rows) {
    const value = Number(row.pnl);
    if (!Number.isFinite(value)) continue;
    const name = key(row);
    const group = book.get(name) ?? { name, trades: 0, wins: 0, pnl: 0 };
    group.trades += 1;
    group.wins += value > 0 ? 1 : 0;
    group.pnl += value;
    book.set(name, group);
  }
  return [...book.values()].sort((a, b) => b.pnl - a.pnl);
}

function money(value: number) {
  const sign = value > 0 ? "+" : value < 0 ? "−" : "";
  return `${sign}$${Math.abs(Math.round(value)).toLocaleString("en-US")}`;
}

function pctText(value: string | number | undefined | null) {
  const number = Number(value);
  if (value == null || value === "" || !Number.isFinite(number)) return "—";
  const sign = number > 0 ? "+" : number < 0 ? "−" : "";
  return `${sign}${Math.abs(Math.round(number))}%`;
}

function share(value: number) {
  return `${Math.round(value * 1000) / 10}%`;
}

function tone(value: number) {
  return value > 0 ? "up" : value < 0 ? "down" : "";
}

const VIEWS: { title: string; note: string; key: (row: Closed) => string }[] = [
  { title: "יום מסחר", note: "לפי יום הסגירה בניו יורק. הימים עם הרווח הגדול למעלה", key: (row) => dayName(row.closed_at) },
  { title: "פתיחת המסחר", note: "לפני 10:00 בניו יורק מול אחרי 10:00", key: (row) => (nyParts(row.opened_at).minutes < 10 * 60 ? "לפני 10:00" : "מ־10:00") },
  { title: "ימים עד פקיעה", note: "כמה ימים נשארו לאופציה ביום הכניסה", key: (row) => dteGroup(row.dte_at_entry) },
  { title: "שעת כניסה", note: "שעון ניו יורק", key: (row) => hourGroup(nyParts(row.opened_at).minutes) },
  { title: "יום בשבוע", note: "יום הכניסה, שעון ניו יורק", key: (row) => WEEKDAYS[nyParts(row.opened_at).weekday] ?? "לא ידוע" },
  { title: "החזקה", note: "נסגרה באותו יום או הוחזקה לילה", key: (row) => (nyParts(row.opened_at).day === nyParts(row.closed_at).day ? "באותו יום" : "הוחזקה לילה") },
  { title: "סיבת יציאה", note: "מה סגר את העסקה", key: (row) => exitLabel(row.exit_reason) },
  { title: "כיוון", note: "CALL או PUT", key: (row) => row.right || "לא ידוע" },
  { title: "מניה", note: "לפי הנכס הבסיסי", key: (row) => row.underlying || "לא ידוע" },
];

type Tab = "trades" | "lab" | "groups";

export default function LearningPage() {
  const [period, setPeriod] = useState<"all" | "month">("all");
  const [tab, setTab] = useState<Tab>("trades");
  const [body, setBody] = useState<TradesBody | null>(null);
  const [failed, setFailed] = useState(false);
  const [open, setOpen] = useState<LearnedTrade | null>(null);

  useEffect(() => {
    let alive = true;
    setBody(null);
    const stop = poll(async () => {
      try {
        const next = await apiGet<TradesBody>(`/api/v1/learning/trades?period=${period}`, 30000);
        if (!alive) return;
        setBody((current) => holdScreen(current, next) ?? next);
        setFailed(false);
      } catch {
        if (alive) setFailed(true);
      }
    }, 60000);
    return () => {
      alive = false;
      stop();
    };
  }, [period]);

  const trades = body?.trades ?? null;
  const total = trades ? groupBy(trades, () => "הכל")[0] : undefined;

  return (
    <Shell>
      <section className="engine book">
        <header className="ops-head">
          <div>
            <h1>{copy.screens.learning}</h1>
            <p>כל עסקה סגורה: שיא, שפל, כמה ויתרנו, ומה היו נותנים כללי יציאה אחרים · PAPER</p>
          </div>
          <div className="ops-pips">
            <button type="button" className={`ops-pip ${period === "all" ? "on" : ""}`} onClick={() => setPeriod("all")}>הכל</button>
            <button type="button" className={`ops-pip ${period === "month" ? "on" : ""}`} onClick={() => setPeriod("month")}>החודש</button>
          </div>
        </header>
        <div className="ops-pips">
          <button type="button" className={`ops-pip ${tab === "trades" ? "on" : ""}`} onClick={() => setTab("trades")}>עסקה-עסקה</button>
          <button type="button" className={`ops-pip ${tab === "lab" ? "on" : ""}`} onClick={() => setTab("lab")}>מעבדת יציאות</button>
          <button type="button" className={`ops-pip ${tab === "groups" ? "on" : ""}`} onClick={() => setTab("groups")}>קבוצות</button>
        </div>
        <p className="ops-line">
          <StaleNote stale={body?.stale} asOf={body?.as_of} />
          {failed && !body
            ? "אין נתונים. השרת לא החזיר את דוח הלמידה."
            : !body
              ? "טוען"
              : body.available === false
                ? "דוח העסקאות לא זמין כרגע מ-Alpaca Paper."
                : total
                  ? `${total.trades} עסקאות סגורות · ${Math.round((total.wins / total.trades) * 100)}% מרוויחות · ${money(total.pnl)} · ל-${body.covered ?? 0} יש מסלול מחירים מוקלט.`
                  : "אין עדיין עסקאות סגורות בתקופה הזו"}
        </p>
        {trades && trades.length && tab === "trades" ? <TradesView trades={trades} onOpen={setOpen} /> : null}
        {trades && tab === "lab" ? <LabView period={period} backfill={body?.backfill} /> : null}
        {trades && trades.length && tab === "groups"
          ? VIEWS.map((view) => (
              <section className="ops-pane" key={view.title}>
                <header className="ops-pane-head">
                  <h2>{view.title}</h2>
                  <span>{view.note}</span>
                </header>
                <div className="plain-list">
                  {groupBy(trades, view.key).map((group) => (
                    <div className="plain-row" key={group.name}>
                      <b><bdi>{group.name}</bdi><small>{group.trades} עסקאות · {Math.round((group.wins / group.trades) * 100)}% מרוויחות</small></b>
                      <strong className={`num ${tone(group.pnl)}`}>{money(group.pnl)}</strong>
                    </div>
                  ))}
                </div>
              </section>
            ))
          : null}
      </section>
      {open ? <TradeModal trade={open} rules={body?.rules} onClose={() => setOpen(null)} /> : null}
    </Shell>
  );
}

function TradesView({ trades, onOpen }: { trades: LearnedTrade[]; onOpen: (trade: LearnedTrade) => void }) {
  const rows = [...trades].sort((a, b) => new Date(b.closed_at).getTime() - new Date(a.closed_at).getTime());
  const giveback = rows.reduce((sum, row) => sum + Math.max(0, Number(row.path.giveback) || 0), 0);
  return (
    <section className="ops-pane">
      <header className="ops-pane-head">
        <h2>עסקה-עסקה</h2>
        <span>שיא ושפל לפי ה-bid בזמן ההחזקה · ויתרנו = שיא פחות מחיר היציאה · לחיצה פותחת גרף</span>
      </header>
      <p className="ops-line">סך הכל ויתרנו על {money(giveback)} בין השיא ליציאה בעסקאות שיש להן שיא ידוע.</p>
      <div className="plain-list">
        {rows.map((row) => {
          const pnl = Number(row.pnl);
          const path = row.path;
          return (
            <button key={`${row.symbol}-${row.opened_at}`} type="button" className="plain-row" onClick={() => onOpen(row)}>
              <b><bdi>{row.underlying} {row.right} {row.strike}</bdi><small>{nyTime(row.opened_at)} · {exitLabel(row.exit_reason)} · שיא {pctText(path.peak_pct)} · ויתרנו {path.giveback != null ? money(Number(path.giveback)) : "—"}</small></b>
              <strong className={`num ${tone(pnl)}`}>{Number.isFinite(pnl) ? money(pnl) : "—"}</strong>
            </button>
          );
        })}
      </div>
    </section>
  );
}

function LabView({ period, backfill }: { period: string; backfill?: Backfill }) {
  const [lab, setLab] = useState<LabBody | null>(null);
  const [failed, setFailed] = useState(false);
  const [status, setStatus] = useState<Backfill | undefined>(backfill);
  const [asked, setAsked] = useState("");

  useEffect(() => {
    let alive = true;
    setLab(null);
    const stop = poll(async () => {
      try {
        const next = await apiGet<LabBody>(`/api/v1/learning/lab?period=${period}`, 60000);
        if (!alive) return;
        setLab(next);
        setFailed(false);
      } catch {
        if (alive) setFailed(true);
      }
    }, 120000);
    return () => {
      alive = false;
      stop();
    };
  }, [period]);

  useEffect(() => {
    if (!status?.running) return;
    const stop = poll(async () => {
      const next = await apiGet<Backfill>("/api/v1/learning/backfill");
      setStatus(next);
    }, 5000);
    return stop;
  }, [status?.running]);

  async function startBackfill() {
    setAsked("שולח");
    try {
      const next = await apiPost<Backfill & { started?: boolean }>(`/api/v1/learning/backfill?period=${period}`);
      setStatus(next);
      setAsked(next.started ? "" : "שחזור כבר רץ");
    } catch (error) {
      setAsked(error instanceof Error ? error.message : "הבקשה נכשלה");
    }
  }

  const current = lab?.current;
  const actual = Number(lab?.actual_pnl);
  return (
    <>
      <section className="ops-pane">
        <header className="ops-pane-head">
          <h2>שחזור מסלולים מ-ThetaData</h2>
          <span>מחירי דקה היסטוריים לעסקאות שנסגרו לפני שהמערכת התחילה להקליט</span>
        </header>
        <p className="ops-line">
          {status?.running
            ? `רץ: ${status.done + status.skipped} מתוך ${status.trades} עסקאות · ${status.marks} מחירים נשמרו`
            : status?.finished_at
              ? `הסתיים: ${status.done} עסקאות עם מסלול · ${status.skipped} ללא נתונים · ${status.marks} מחירים${status.error ? ` · שגיאה: ${status.error}` : ""}`
              : "עוד לא הורץ. עסקאות חדשות מוקלטות אוטומטית מהמוניטור."}
        </p>
        <button type="button" className="ops-pip" onClick={startBackfill} disabled={status?.running}>שחזר מסלולים לעסקאות עבר</button>
        {asked ? <p className="ops-line">{asked}</p> : null}
      </section>
      <section className="ops-pane">
        <header className="ops-pane-head">
          <h2>מעבדת יציאות</h2>
          <span>כל צירוף כללים מורץ על מסלולי ה-bid האמיתיים · כל עסקה נסגרת לכל המאוחר 15 דקות לפני סוף היום</span>
        </header>
        <p className="ops-line">
          {failed && !lab
            ? "אין נתונים. המעבדה לא חזרה מהשרת."
            : !lab
              ? "מחשב"
              : lab.available === false
                ? "דוח העסקאות לא זמין כרגע."
                : !lab.trades_covered
                  ? "אין עדיין עסקאות עם מסלול מחירים. הרץ שחזור או חכה לעסקאות הבאות."
                  : `${lab.trades_covered} מתוך ${lab.trades_total} עסקאות עם מסלול · בפועל ${money(actual)} · הכללים הנוכחיים בסימולציה ${current ? money(Number(current.pnl)) : "—"} (מקום ${current?.rank ?? "—"} מתוך ${lab.combinations}). "חלקי" = המסלול נגמר לפני שכלל נורה, ונשאר מחיר היציאה בפועל.`}
        </p>
        {lab?.best?.length ? (
          <div className="plain-list">
            {[...lab.best, ...(current && !lab.best.some((row) => row.current) ? [current] : [])].map((row) => {
              const pnl = Number(row.pnl);
              const delta = current ? pnl - Number(current.pnl) : 0;
              return (
                <div className="plain-row" key={`${row.rank}`}>
                  <b>מקום {row.rank}{row.current ? " · נוכחי" : ""}<small>סטופ −{share(row.stop_pct)} · נגרר +{share(row.trail_arm_pct)} במרחק {share(row.trail_pct)} · ביטול {row.invalidation_pct ? share(row.invalidation_pct) : "כבוי"} · {row.wins}/{row.trades} מרוויחות</small></b>
                  <strong className={`num ${tone(pnl)}`}>{money(pnl)}{row.current ? "" : ` · ${money(delta)}`}</strong>
                </div>
              );
            })}
          </div>
        ) : null}
        <p className="ops-line">מעט עסקאות עם מסלול = כיוון בלבד. לא משנים סטופים לפני שיש מספיק עסקאות ואישור שלך.</p>
      </section>
    </>
  );
}

function TradeModal({ trade, rules, onClose }: { trade: LearnedTrade; rules?: Rules; onClose: () => void }) {
  const [points, setPoints] = useState<PathPoint[] | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    const query = new URLSearchParams({ symbol: trade.symbol, opened_at: trade.opened_at, closed_at: trade.closed_at });
    apiGet<{ points?: PathPoint[] }>(`/api/v1/learning/path?${query.toString()}`, 30000)
      .then((body) => setPoints(body.points ?? []))
      .catch(() => setFailed(true));
  }, [trade]);

  const pnl = Number(trade.pnl);
  const path = trade.path;
  const replay = trade.current_rules;
  const lines: [string, string][] = [
    ["כניסה", `${nyTime(trade.opened_at)} · $${trade.entry_price}`],
    ["יציאה", `${nyTime(trade.closed_at)} · $${trade.exit_price} · ${exitLabel(trade.exit_reason)}`],
    ["שיא בזמן ההחזקה", path.peak_price ? `$${path.peak_price} (${pctText(path.peak_pct)})${path.peak_minutes != null ? ` אחרי ${Math.round(path.peak_minutes)} דקות` : ""}` : "—"],
    ["שפל בזמן ההחזקה", path.trough_price ? `$${path.trough_price} (${pctText(path.trough_pct)})${path.trough_minutes != null ? ` אחרי ${Math.round(path.trough_minutes)} דקות` : ""}` : "—"],
    ["ויתרנו בין השיא ליציאה", path.giveback != null ? money(Number(path.giveback)) : "—"],
    ["אחרי היציאה עד סוף היום", path.after_best_pct != null ? `הכי גבוה ${pctText(path.after_best_pct)} · הכי נמוך ${pctText(path.after_worst_pct)} · בסגירה ${pctText(path.after_last_pct)}` : "לא הוקלט"],
    ["לפי הכללים הנוכחיים", replay ? `${exitLabel(replay.reason)} ב-$${replay.exit_price} · ${money(Number(replay.pnl))}${replay.complete ? "" : " (חלקי)"}` : "אין מסלול"],
  ];
  return (
    <div className="engine-backdrop" onClick={onClose}>
      <article className="engine-modal" role="dialog" aria-modal="true" aria-labelledby="learn-title" onClick={(event) => event.stopPropagation()}>
        <header>
          <div>
            <p>עסקה סגורה · PAPER</p>
            <h2 id="learn-title" className={tone(pnl)}>
              <bdi>{trade.underlying} {trade.right} {trade.strike}</bdi> · {Number.isFinite(pnl) ? money(pnl) : "—"}
            </h2>
          </div>
          <button type="button" onClick={onClose}>סגור</button>
        </header>
        {failed ? <p>המסלול לא חזר מהשרת.</p> : !points ? <p>טוען מסלול</p> : points.length < 2 ? <p>אין מסלול מחירים מוקלט לעסקה הזו.</p> : <PathChart points={points} trade={trade} rules={rules} />}
        <pre>{lines.map(([label, value]) => `${label}: ${value}`).join("\n")}</pre>
      </article>
    </div>
  );
}

function PathChart({ points, trade, rules }: { points: PathPoint[]; trade: LearnedTrade; rules?: Rules }) {
  const width = 640;
  const height = 240;
  const pad = 28;
  const entry = Number(trade.entry_price);
  const exit = Number(trade.exit_price);
  const times = points.map((point) => new Date(point.at).getTime());
  const bids = points.map((point) => Number(point.bid));
  const stop = rules ? entry * (1 - rules.stop_pct) : null;
  const arm = rules ? entry * (1 + rules.trail_arm_pct) : null;
  const levels = [entry, exit, ...(stop ? [stop] : []), ...(arm ? [arm] : [])].filter(Number.isFinite);
  const low = Math.min(...bids, ...levels) * 0.97;
  const high = Math.max(...bids, ...levels) * 1.03;
  const start = times[0];
  const end = Math.max(times[times.length - 1], new Date(trade.closed_at).getTime());
  const x = (time: number) => pad + ((time - start) / Math.max(1, end - start)) * (width - pad * 2);
  const y = (price: number) => height - pad - ((price - low) / Math.max(0.0001, high - low)) * (height - pad * 2);
  const closedAt = new Date(trade.closed_at).getTime();
  const held = points.filter((point) => new Date(point.at).getTime() <= closedAt);
  const after = points.filter((point) => new Date(point.at).getTime() >= closedAt);
  const line = (rows: PathPoint[]) => rows.map((point) => `${x(new Date(point.at).getTime()).toFixed(1)},${y(Number(point.bid)).toFixed(1)}`).join(" ");
  const peak = held.reduce<PathPoint | null>((best, point) => (!best || Number(point.bid) > Number(best.bid) ? point : best), null);
  const level = (price: number | null, label: string, color: string) =>
    price == null || !Number.isFinite(price) ? null : (
      <g key={label}>
        <line x1={pad} x2={width - pad} y1={y(price)} y2={y(price)} stroke={color} strokeDasharray="4 4" strokeWidth={1} />
        <text x={width - pad} y={y(price) - 4} fill={color} fontSize={11} textAnchor="end">{label} ${price.toFixed(2)}</text>
      </g>
    );
  return (
    <figure style={{ margin: "8px 0", direction: "ltr" }}>
      <svg viewBox={`0 0 ${width} ${height}`} style={{ width: "100%", height: "auto" }} role="img" aria-label="מסלול מחיר ה-bid">
        {level(entry, "כניסה", "#9aa4b2")}
        {level(stop, "סטופ", "#e5484d")}
        {level(arm, "נגרר", "#30a46c")}
        <line x1={x(closedAt)} x2={x(closedAt)} y1={pad} y2={height - pad} stroke="#f5a524" strokeWidth={1} />
        <text x={x(closedAt) + 4} y={pad + 10} fill="#f5a524" fontSize={11}>יציאה ${Number.isFinite(exit) ? exit.toFixed(2) : ""}</text>
        <polyline points={line(held)} fill="none" stroke="#4c8dff" strokeWidth={2} />
        {after.length > 1 ? <polyline points={line(after)} fill="none" stroke="#4c8dff" strokeWidth={1.5} strokeDasharray="3 3" opacity={0.7} /> : null}
        {peak ? <circle cx={x(new Date(peak.at).getTime())} cy={y(Number(peak.bid))} r={4} fill="#30a46c" /> : null}
        <text x={pad} y={height - 8} fill="#9aa4b2" fontSize={11}>{nyClock(points[0].at)}</text>
        <text x={width - pad} y={height - 8} fill="#9aa4b2" fontSize={11} textAnchor="end">{nyClock(points[points.length - 1].at)}</text>
      </svg>
      <figcaption style={{ direction: "rtl", fontSize: 12, opacity: 0.75 }}>
        קו מלא = ה-bid בזמן ההחזקה · מקווקו = אחרי היציאה עד סוף היום · נקודה ירוקה = השיא · {points.length} מחירים ({[...new Set(points.map((point) => point.source))].join(", ")})
      </figcaption>
    </figure>
  );
}
