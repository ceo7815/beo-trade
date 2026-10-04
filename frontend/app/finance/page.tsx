"use client";

import { useEffect, useState } from "react";
import { Shell } from "@/components/Shell";
import { apiGet } from "@/lib/api";

type Summary = { enough?: boolean; note?: string | null; count?: number; net?: string | null; gross?: string | null; fees_note?: string | null; win_rate?: string | null; profit_factor?: string | null; average_win?: string | null; average_loss?: string | null };

type Finance = {
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
type Explain = { title: string; does: string; tools: string[]; formula: string };
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
  const [exitReason, setExitReason] = useState("");
  const [hour, setHour] = useState("");
  const [dte, setDte] = useState("");
  const [menu, setMenu] = useState<string | null>(null);
  const [body, setBody] = useState<Finance | null>(null);
  const [maxPositions, setMaxPositions] = useState<number | null>(null);
  const [failed, setFailed] = useState(false);
  const [open, setOpen] = useState<Explain | null>(null);

  useEffect(() => {
    let alive = true;
    const load = () => {
      const query = new URLSearchParams({ window: windowName, side, outcome, symbol, dte, start, end, exit_reason: exitReason, hour });
      Promise.allSettled([
        apiGet<Finance>(`/api/v1/finance?${query.toString()}`),
        apiGet<Risk>("/api/v1/risk"),
      ]).then(([financeResult, riskResult]) => {
        if (!alive) return;
        if (financeResult.status === "fulfilled") {
          setBody(financeResult.value);
          setFailed(false);
        } else {
          setFailed(true);
        }
        setMaxPositions(riskResult.status === "fulfilled" ? riskResult.value.max_positions ?? null : null);
      });
    };
    load();
    const timer = window.setInterval(load, 12000);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, [windowName, side, outcome, symbol, dte, start, end, exitReason, hour]);

  useEffect(() => {
    if (!open && !menu) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setOpen(null);
        setMenu(null);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, menu]);

  const account = body?.account;
  const periods = body?.periods;
  const positions = body?.open_positions;
  const cubes: { label: string; value: string; explain: Explain }[] = [
    cube("הון עצמי", money(account?.equity), "ההון בחשבון Alpaca Paper. אותו מספר שמופיע בלוח הראשי.", ["Alpaca account"], "equity"),
    cube("מזומן", money(account?.cash), "מזומן פנוי בחשבון Alpaca Paper.", ["Alpaca cash"], "cash"),
    cube("כוח קנייה", money(account?.buying_power), "כוח הקנייה של חשבון ה-PAPER. הוא אינו מזומן.", ["Alpaca buying_power"], "buying_power"),
    cube("שווי תיק", money(account?.portfolio_value), "שווי התיק אצל Alpaca Paper.", ["Alpaca portfolio_value"], "portfolio_value"),
    cube("חשיפה", money(body?.exposure), "שווי השוק של הפוזיציות הפתוחות. אותו מספר שמופיע בפוזיציות.", ["Alpaca market_value"], "חשיפה = Σ |market_value|"),
    cube("פוזיציות", positions == null ? "אין נתונים" : maxPositions == null ? String(positions) : `${positions} / ${maxPositions}`, "כמה חוזים פתוחים עכשיו, מול התקרה. אותו יחס שמופיע בפוזיציות.", ["Alpaca positions"], "פתוח = len(positions)\nתקרה = 5"),
    cube("רווח היום", money(body?.today_pnl), "רווח מסחר מאז תחילת יום המסחר, אחרי הפקדות, משיכות והעברות. בלי בסיס פעילויות הנתון נשאר ריק.", ["Alpaca activities", "equity", "last_equity"], "היום = (equity − last_equity) − מימון של אותו יום"),
    cube("הון פנוי", money(account?.cash), "המזומן הפנוי. זה שדה cash של Alpaca Paper.", ["Alpaca cash"], "הון פנוי = cash"),
    cube("אתמול", money(periods?.yesterday), "שינוי סדרת profit_loss בין שתי הנקודות שלפני האחרונה.", ["Alpaca portfolio history"], "אתמול = נקודה[−2] − נקודה[−3]"),
    cube("שבוע", money(periods?.week), "שינוי profit_loss בשבעת הימים האחרונים.", ["Alpaca portfolio history"], "שבוע = סוף − ערך לפני 7 ימים"),
    cube("חודש", money(periods?.month), "שינוי profit_loss ב-30 הימים האחרונים.", ["Alpaca portfolio history"], "חודש = סוף − ערך לפני 30 ימים"),
    cube("רבעון", money(periods?.quarter), "שינוי profit_loss ב-90 הימים האחרונים.", ["Alpaca portfolio history"], "רבעון = סוף − ערך לפני 90 ימים"),
    cube("שנה", money(periods?.year), "שינוי profit_loss ב-365 הימים האחרונים.", ["Alpaca portfolio history"], "שנה = סוף − ערך לפני 365 ימים"),
    cube("מצטבר", money(periods?.cumulative), "הפרש profit_loss בין הנקודה האחרונה לראשונה. קפיצת מימון אינה רווח.", ["Alpaca profit_loss"], "מצטבר = אחרון − ראשון"),
    cube("ניצול", percent(body?.utilization), "כמה מההון תפוס בחשיפה פתוחה.", ["חשיפה", "equity"], "ניצול = חשיפה / הון"),
    cube("משיכת שיא", body?.drawdown.enough ? money(body.drawdown.maximum) : "אין נתונים", "הירידה הגדולה ביותר מהשיא בסדרת ההון.", ["Alpaca equity"], "משיכה = שיא − הון"),
  ];
  const rows = [cubes.slice(0, 4), cubes.slice(4, 8), cubes.slice(8, 12), cubes.slice(12)];

  return (
    <Shell>
      <section className="engine" onClick={() => setMenu(null)}>
        <header className="ops-head">
          <div>
            <h1>בקרת כספים</h1>
            <p>Financial Control · PAPER</p>
          </div>
          <span className={`ops-pip ${failed ? "warn" : body ? "on" : "warn"}`}>
            <i />
            {failed ? "אין נתונים" : body ? "Alpaca Paper" : "טוען"}
          </span>
        </header>
        <p className="ops-line">{body?.paper_limitation || "ביצוע Paper אינו משחזר השפעת שוק, מיקום בתור, דליפת מידע או slippage של לייב."}</p>

        <div className="control-drawer" onClick={(event) => event.stopPropagation()}>
          <p>בחירה</p>
          <div className="control-filters">
            <Menu name="window" label="טווח" value={windowName} options={WINDOWS} menu={menu} setMenu={setMenu} onChange={setWindowName} />
            <Menu name="side" label="צד" value={side} options={[{ id: "", label: "CALL ו-PUT" }, { id: "CALL", label: "CALL" }, { id: "PUT", label: "PUT" }]} menu={menu} setMenu={setMenu} onChange={setSide} />
            <Menu name="outcome" label="תוצאה" value={outcome} options={[{ id: "", label: "כל העסקאות" }, { id: "win", label: "רווחיות" }, { id: "loss", label: "מפסידות" }]} menu={menu} setMenu={setMenu} onChange={setOutcome} />
            <Menu name="dte" label="DTE" value={dte} options={[{ id: "", label: "כל ה-DTE" }, { id: "0DTE", label: "0DTE" }, { id: "1DTE", label: "1DTE" }, { id: "2DTE+", label: "2DTE+" }]} menu={menu} setMenu={setMenu} onChange={setDte} />
            <label className="menu-field">
              <span>נכס</span>
              <input placeholder="הכול" value={symbol} onChange={(event) => setSymbol(event.target.value.toUpperCase())} />
            </label>
            <label className="menu-field">
              <span>מתאריך</span>
              <input type="date" value={start} onChange={(event) => setStart(event.target.value)} />
            </label>
            <label className="menu-field">
              <span>עד תאריך</span>
              <input type="date" value={end} onChange={(event) => setEnd(event.target.value)} />
            </label>
            <Menu name="exit" label="יציאה" value={exitReason} options={[{ id: "", label: "כל הסיבות" }, { id: "STOP", label: "STOP" }, { id: "TAKE_PROFIT", label: "TAKE_PROFIT" }, { id: "SESSION_CLOSE", label: "SESSION_CLOSE" }, { id: "EXPIRATION", label: "EXPIRATION" }]} menu={menu} setMenu={setMenu} onChange={setExitReason} />
            <label className="menu-field">
              <span>שעה</span>
              <input placeholder="הכול" value={hour} onChange={(event) => setHour(event.target.value)} />
            </label>
          </div>
        </div>

        <div className="engine-board" aria-label="בקרת כספים">
          {rows.map((row, rowIndex) => (
            <div className="engine-row fit" key={row[0]?.label ?? rowIndex}>
              {row.map((item, index) => (
                <button
                  key={item.label}
                  type="button"
                  className="engine-cube stat"
                  style={{ animationDelay: `${(rowIndex * 4 + index) * 30}ms` }}
                  onClick={() => setOpen(item.explain)}
                >
                  <span>{item.label}</span>
                  <b className="num">{item.value}</b>
                </button>
              ))}
            </div>
          ))}
        </div>

        <CubeGrid
          label="ניתוח"
          items={[
            { title: "ניתוח רווחיות", value: body?.trades.fees_note ? body.trades.fees_note : body?.trades.enough ? money(body.trades.net) : body?.trades.note || "אין מספיק מידע לניתוח", explain: { title: "ניתוח רווחיות", does: "ברוטו מגיע ממחירי המילוי. נטו מוצג רק כשיש עמלת ברוקר.", tools: ["Alpaca FILL"], formula: body?.trades.fees_note ? `רווח/הפסד ברוטו\n${money(body.trades.gross)}\nרווח/הפסד נטו\nלא זמין עדיין\nעמלות טרם זמינות` : body?.trades.enough ? `נקי ${money(body.trades.net)}\nשיעור רווח ${percent(body.trades.win_rate)}` : "ברוטו = (יציאה − כניסה) × כמות × 100\nנטו רק כשיש עמלה" } },
            { title: "CALL", value: sideValue(body?.by_side.CALL), explain: sideExplain("CALL", body?.by_side.CALL) },
            { title: "PUT", value: sideValue(body?.by_side.PUT), explain: sideExplain("PUT", body?.by_side.PUT) },
            ...["0DTE", "1DTE", "2DTE+"].map((key) => ({ title: key, value: sideValue(body?.by_dte[key]), explain: { title: key, does: "פילוח העסקאות הסגורות לפי ימים לפקיעה.", tools: ["פקיעת OCC"], formula: "0DTE אם DTE ≤ 0\n1DTE אם DTE = 1\nאחרת 2DTE+" } })),
            { title: "סקטור", value: body?.sector.note || "נתוני סקטור אינם זמינים", explain: { title: "סקטור", does: "אין מקור סקטור אמיתי בחשבון Alpaca. אין כאן שיוך מומצא.", tools: ["Alpaca assets"], formula: "סקטור = לא זמין" } },
            { title: "שעות", value: body?.hours?.note || "אין מספיק נתונים", explain: { title: "פילוח שעות", does: "אין עדיין מספיק עסקאות סגורות כדי לפלח לפי שעה.", tools: ["מילויים"], formula: "שעה = שעת המילוי באזור Asia/Dubai" } },
            { title: "סיבות יציאה", value: body?.exits?.note || "אין מספיק נתונים", explain: { title: "סיבות יציאה", does: "היציאה נקבעת במנוע לפי הסדר: חירום, סשן, פקיעה, ביטול תזה, נזילות, סטופ, trailing, זמן.", tools: ["מנוע יציאה"], formula: "סטופ = כניסה × 0.60\ntrailing = שיא × 0.85\n0DTE ≤ 90 דקות\n1DTE ≤ 180 דקות" } },
            { title: "הטוב והגרוע", value: body?.best_worst.note || "אין מספיק מידע לניתוח", explain: { title: "הטוב והגרוע", does: "מוצג רק כשיש לפחות שלוש עסקאות סגורות עם רווח מחושב.", tools: ["עסקאות סגורות"], formula: "נדרשות ≥ 3 עסקאות" } },
          ]}
          onOpen={setOpen}
        />

        <article className="engine-cube wide">
          <span>עקומת הון</span>
          <Curve points={body?.history?.points || []} />
          <b>היסטוריית תיק Alpaca Paper · {WINDOWS.find((item) => item.id === windowName)?.label}</b>
        </article>

        <CubeGrid
          label="ימים וחודשים"
          items={[
            ...(body?.daily || []).slice(-8).map((row) => ({
              title: row.date,
              value: money(row.pnl),
              explain: { title: row.date, does: "שינוי ההון בתוך יום אחד. נספר רק כשיש שתי נקודות באותו יום.", tools: ["Alpaca equity"], formula: `התחלה ${money(row.start)}\nסיום ${money(row.end)}\nיום = סיום − התחלה` },
            })),
            ...(body?.monthly || []).slice(-6).map((row) => ({
              title: row.month,
              value: money(row.pnl),
              explain: { title: row.month, does: "שינוי profit_loss בתוך החודש.", tools: ["Alpaca profit_loss"], formula: "חודש = נקודה אחרונה − נקודה ראשונה בחודש" },
            })),
            ...Object.entries(body?.by_symbol || {}).map(([name, row]) => ({
              title: name,
              value: row.enough ? money(row.net) : "אין מספיק נתונים",
              explain: { title: name, does: "רווח העסקאות הסגורות בנכס הזה.", tools: ["Alpaca FILL"], formula: "נקי = Σ רווח העסקאות בנכס" },
            })),
          ]}
          onOpen={setOpen}
        />
      </section>

      {open ? (
        <div className="engine-backdrop" onClick={() => setOpen(null)}>
          <article className="engine-modal" role="dialog" aria-modal="true" aria-labelledby="finance-title" onClick={(event) => event.stopPropagation()}>
            <header>
              <div>
                <p>בקרת כספים · PAPER</p>
                <h2 id="finance-title">{open.title}</h2>
              </div>
              <button type="button" onClick={() => setOpen(null)}>סגור</button>
            </header>
            <h3>מה הנתון</h3>
            <p>{open.does}</p>
            <h3>כלים</h3>
            <ul>{open.tools.map((tool) => <li key={tool}>{tool}</li>)}</ul>
            <h3>נוסחה</h3>
            <pre>{open.formula}</pre>
          </article>
        </div>
      ) : null}
    </Shell>
  );
}

function cube(label: string, value: string, does: string, tools: string[], formula: string) {
  return { label, value, explain: { title: label, does, tools, formula } };
}

function sideValue(row?: Summary) {
  return row?.enough ? `${row.count ?? 0} · ${money(row.net)}` : "אין מספיק נתונים";
}

function sideExplain(title: string, row?: Summary): Explain {
  return {
    title,
    does: "פילוח העסקאות הסגורות לפי צד האופציה.",
    tools: ["סימול OCC"],
    formula: row?.enough ? `עסקאות ${row.count ?? 0}\nנקי ${money(row.net)}` : "אין עסקה סגורה בצד הזה",
  };
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

function CubeGrid({ label, items, onOpen }: { label: string; items: { title: string; value: string; explain: Explain }[]; onOpen: (item: Explain) => void }) {
  const lines = [];
  for (let index = 0; index < items.length; index += 4) lines.push(items.slice(index, index + 4));
  if (lines.length === 0) return null;
  return (
    <div className="engine-board" aria-label={label}>
      {lines.map((line, rowIndex) => (
        <div className="engine-row fit" key={line[0]?.title ?? rowIndex}>
          {line.map((item) => (
            <button key={item.title} type="button" className="engine-cube" onClick={() => onOpen(item.explain)}>
              <span>{item.title}</span>
              <b>{item.value}</b>
            </button>
          ))}
        </div>
      ))}
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
