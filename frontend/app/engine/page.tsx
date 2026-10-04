"use client";

import { useEffect, useState } from "react";
import { Shell } from "@/components/Shell";
import { apiGet } from "@/lib/api";

type Desk = {
  autonomous: boolean;
  last_scan: {
    observed_at?: string;
    recommendations?: number;
    buys?: number;
    ai_calls?: number;
    blocked?: string;
    universe?: { discovered?: number; filtered?: number; status?: string } | null;
    stages?: Record<string, number | null>;
  } | null;
};

type Stage = {
  id: string;
  title: string;
  does: string;
  tools: string[];
  formula: string;
};

const STAGES: Stage[] = [
  {
    id: "scan",
    title: "סריקת שוק",
    does: "בונה את יקום המניות מ-Alpaca ולוקח פלח מסתובב לכל סריקה. ThetaData הוא מקור המחיר והשרשרת, לא רשימה קבועה.",
    tools: ["Alpaca Assets", "ThetaData", "יקום מסתובב"],
    formula: "נשמר אם status=active ו-tradable ו-us_equity ו-has_options.\nפלח = 40 מניות. רענון יקום = 21600 שניות.",
  },
  {
    id: "filter",
    title: "סינון ראשוני",
    does: "פוסל נכס בסיס לפני שרשרת אופציות אם הציטוט ישן, הסשן סגור, או שאין מספיק מחזור ותנועה.",
    tools: ["ThetaData quote", "שעון מסחר"],
    formula: "גיל ציטוט ≤ 20 שניות\nמחזור ≥ 1,000,000\nRVOL ≥ 1.8\n|שינוי| ≥ 0.6%",
  },
  {
    id: "options",
    title: "ניתוח אופציות",
    does: "משאיר חוזה רק אם הציטוט, המרווח, הנזילות, הפקיעה והיווניות עומדים בספים.",
    tools: ["ThetaData option quote", "Greeks", "Open interest"],
    formula: "spread = (ask − bid) / mid ≤ 0.10\nמחזור ≥ 500 · OI ≥ 200\n0.30 ≤ mid ≤ 20\n0 ≤ DTE ≤ 5\n0.30 ≤ |delta| ≤ 0.60\ngamma ≥ 0.005\n|theta| / mid ≤ 0.35",
  },
  {
    id: "quant",
    title: "Quant",
    does: "מחשב מחיר תיאורטי ויווניות ב-Black-Scholes, ותנועה צפויה מה-IV. אינו יוצר עסקה לבד.",
    tools: ["Black-Scholes", "expected move"],
    formula: "d1 = [ln(S/K) + (r − q + ½σ²)T] / (σ√T)\nCall = S·e^(−qT)·N(d1) − K·e^(−rT)·N(d2)\nexpected move = S · σ · √(days/365)",
  },
  {
    id: "intel",
    title: "News / SEC / FRED / Regime",
    does: "חדשות חייבות אישור משני מקורות או ממקור רשמי. משטר השוק חוסם צד, ואינו יוצר BUY.",
    tools: ["Benzinga", "SEC EDGAR", "FRED", "SPY QQQ IWM VIX"],
    formula: "NEWS אם מקורות ≥ 2 או credibility ≥ 0.90\nגיל חדשות ≤ 21600 שניות\nUP אם SPY,QQQ,IWM > 0.15% · DOWN אם כולם < −0.15%\nVIX < 20 רגוע · ≥ 20 מוגבר · ≥ 28 לחוץ\nלחוץ חוסם CALL ו-PUT. UP חוסם PUT. DOWN חוסם CALL.",
  },
  {
    id: "ai",
    title: "AI",
    does: "קריאה אחת לכל טביעת אצבע. הפלט הוא תזה, סיכון ותנאי ביטול. הכמות והמחיר אינם נקבעים כאן.",
    tools: ["OpenAI Responses", "fingerprint", "נעילה אטומית"],
    formula: "עד 8 קריאות לסריקה\nTTL החלטה = 900 שניות\nפלט ≤ 800 טוקנים\ndata_quality = FAIL פוסל AI_OK\nמעבר לתקרה: SUPPRESS · AI_BUDGET_EXCEEDED",
  },
  {
    id: "risk",
    title: "Risk",
    does: "קובע כמות חוזים מאחוז ההון. חוסם BUY חדש בהפסד יומי, בחשיפת סקטור, או כשאין מקום.",
    tools: ["הון Alpaca Paper", "פוזיציות פתוחות"],
    formula: "תקציב סיכון = הון × 2%\nעלות חוזה = ask × 100\nסיכון לחוזה = עלות × 40%\nכמות = min(סיכון, הון 7%, תקרה 10%, חשיפה 35%, סקטור 10%, סיכון מצטבר 15%)\nהפסד יומי: אזהרה 2.5% · עצירת כניסות 4% · עצירה 5%",
  },
  {
    id: "execute",
    title: "ביצוע אוטומטי",
    does: "BUY שעבר את כל השערים נשלח כפקודת limit ב-PAPER אחרי בדיקה מחדש. אין אישור משתמש במסלול הזה.",
    tools: ["Alpaca Paper", "בדיקה מחדש"],
    formula: "buy_to_open · limit · day\nציטוט טרי, מרווח, IV, חדשות, משטר, סשן, כוח קנייה\nסטיית mid מול המחיר ≤ 3%\nגיל המלצה ≤ 180 שניות",
  },
  {
    id: "manage",
    title: "ניהול פוזיציה",
    does: "המוניטור קורא פוזיציות Alpaca וציטוט ThetaData. בלי ציטוט אין מחיר ואין יציאה.",
    tools: ["Alpaca positions", "ThetaData", "שעון Alpaca"],
    formula: "מחזור מוניטור = 15 שניות\nis_open חייב להיות True או False\nציטוט חסר = אין סימון ואין פקודה",
  },
  {
    id: "exit",
    title: "יציאה אוטומטית",
    does: "יציאה נבדקת לפי הסדר הזה. מחיר היציאה הוא ה-bid. אין AI ביציאה.",
    tools: ["מנוע יציאה", "Alpaca sell_to_close"],
    formula: "סדר: kill · סשן · פקיעה · ביטול תזה · נזילות · סטופ · trailing · זמן\nסטופ = כניסה × 0.60\nב-+1R הסטופ עובר ל-−0.25R\nב-+1.5R trailing = שיא × 0.85\n+2R אינו סוגר לבד\n0DTE ≤ 90 דקות · 1DTE ≤ 180 דקות\nבלי החזקה ללילה",
  },
  {
    id: "pnl",
    title: "רווח/הפסד",
    does: "רווח היום נלקח מההון מול ההון הקודם. עסקה סגורה נספרת רק כשיש מחיר כניסה ומחיר יציאה.",
    tools: ["Alpaca equity", "last_equity", "fills"],
    formula: "היום = (equity − last_equity) − מימון של יום המסחר\nגולמי = (יציאה − כניסה) × כמות × 100\nנטו = גולמי − עמלה רק כשהעמלה קיימת\nבלי עמלה: עמלות טרם זמינות",
  },
  {
    id: "audit",
    title: "ביקורת",
    does: "כל מעבר מצב של פקודה נשמר: חסימה, אימות מערכת, שליחה ומילוי. אין כאן שרשרת מחשבה של המודל.",
    tools: ["broker_trade_events", "ai_request_logs"],
    formula: "אירוע = מצב + מקור + סיבה + זמן\nטוקן חסר נשמר כריק, לא כמספר מומצא",
  },
];

export default function EnginePage() {
  const [desk, setDesk] = useState<Desk | null>(null);
  const [open, setOpen] = useState<Stage | null>(null);

  useEffect(() => {
    let alive = true;
    const load = () => apiGet<Desk>("/api/v1/desk").then((body) => alive && setDesk(body)).catch(() => alive && setDesk(null));
    load();
    const timer = window.setInterval(load, 12000);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, []);

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  const scan = desk?.last_scan;
  const stages = scan?.stages;
  const counts = [
    ["יקום נבדק", stages?.universe_checked ?? scan?.universe?.discovered],
    ["יקום עבר", stages?.universe_passed ?? scan?.universe?.filtered],
    ["נכסים עברו", stages?.underlyings_passed],
    ["חוזים נבדקו", stages?.contracts_checked],
    ["חוזים עברו", stages?.contracts_passed],
    ["חדשות עברו", stages?.news_passed],
    ["סיכון עבר", stages?.risk_passed],
    ["קריאות AI", stages?.ai_calls ?? scan?.ai_calls],
    ["החלטות BUY", stages?.buys ?? scan?.buys],
  ] as const;

  return (
    <Shell>
      <section className="engine">
        <header className="ops-head">
          <div>
            <h1>המערכת האוטונומית</h1>
            <p>Autonomous Engine · PAPER</p>
          </div>
          <span className={`ops-pip ${desk ? (desk.autonomous ? "on" : "off") : "warn"}`}>
            <i />
            {desk ? (desk.autonomous ? "המנוע פועם" : "המנוע לא פעיל") : "אין נתונים"}
          </span>
        </header>
        <p className="ops-line">ביצוע Paper אינו משחזר השפעת שוק, מיקום בתור, דליפת מידע או slippage של לייב. רווח Paper אינו ראיה לרווח בלייב.</p>

        <div className="engine-row" aria-label="מוני סריקה">
          {counts.map(([label, value], index) => (
            <article className="engine-cube stat" key={label} style={{ animationDelay: `${index * 40}ms` }}>
              <span>{label}</span>
              <b className="num">{value == null ? "אין נתונים" : value}</b>
            </article>
          ))}
        </div>

        <div className="engine-stages" aria-label="שלבי המנוע">
          {STAGES.map((stage, index) => (
            <button
              key={stage.id}
              type="button"
              className="engine-stage"
              style={{ animationDelay: `${120 + index * 35}ms` }}
              onClick={() => setOpen(stage)}
            >
              <span>{stage.title}</span>
              <b>{scan?.blocked ? "חסום" : "אין נתונים"}</b>
            </button>
          ))}
        </div>

        {scan?.blocked ? <p className="ops-line">{scan.blocked}</p> : null}
        {!scan ? <p className="ops-line">אין עדיין היסטוריית סריקה. לחצו על שלב כדי לראות את הנוסחה.</p> : null}
      </section>

      {open ? (
        <div className="engine-backdrop" onClick={() => setOpen(null)}>
          <article className="engine-modal" role="dialog" aria-modal="true" aria-labelledby="stage-title" onClick={(event) => event.stopPropagation()}>
            <header>
              <div>
                <p>שלב במנוע · PAPER</p>
                <h2 id="stage-title">{open.title}</h2>
              </div>
              <button type="button" onClick={() => setOpen(null)}>סגור</button>
            </header>
            <h3>מה הוא עושה</h3>
            <p>{open.does}</p>
            <h3>כלים</h3>
            <ul>
              {open.tools.map((tool) => <li key={tool}>{tool}</li>)}
            </ul>
            <h3>נוסחה</h3>
            <pre>{open.formula}</pre>
          </article>
        </div>
      ) : null}
    </Shell>
  );
}
