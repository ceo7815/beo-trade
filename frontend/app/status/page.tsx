"use client";

import { useEffect, useState } from "react";
import { Shell } from "@/components/Shell";
import { PHASES, apiGet, type SystemStatus } from "@/lib/api";

type Tone = "ok" | "warn" | "bad" | "wait";

const RULES: Record<string, string> = {
  "Market Data": "תקין רק אחרי ציטוט אמיתי. מפתח בלי ציטוט נשאר ממתין.",
  News: "תקין אחרי אימות מפתח ותשובה אמיתית. מפתח שמור לבד אינו חיבור.",
  FRED: "תקין רק כשיש תצפית מספרית. זהות בלי מספר אינה חיבור.",
  "SEC EDGAR": "תקין רק כשיש הגשות ועובדות חברה.",
  AI: "תקין כשהמצב אינו עצור ואינו חורג מהתקציב.",
  Supabase: "מסד הריצה הוא SQLite. אין כאן חיבור Supabase פעיל.",
  "מסד נתונים": "תקין כשהמסד המקומי עונה.",
  Scanner: "תקין כשגיל הדופק אינו מעל 180 שניות.",
  "Quant Engine": "המנוע קיים בקוד. אין כאן בדיקת חיבור נפרדת.",
  "Paper Trading": "תקין כשמצב המסחר הוא PAPER.",
};

export default function StatusPage() {
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [failed, setFailed] = useState(false);
  const [flags, setFlags] = useState<Record<string, boolean> | null>(null);
  const [open, setOpen] = useState<{ title: string; does: string } | null>(null);

  useEffect(() => {
    let alive = true;
    const load = () => {
      apiGet<SystemStatus>("/api/v1/system").then((body) => alive && setStatus(body)).catch(() => alive && setFailed(true));
      apiGet<{ flags: Record<string, boolean> }>("/api/v1/broker/alpaca/health").then((body) => alive && setFlags(body.flags)).catch(() => alive && setFlags(null));
    };
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

  const rows = status ? healthRows(status) : [];
  const phase = status ? PHASES[status.market_phase] || status.market_phase : "טוען";
  const cubes = [
    { label: "שלב שוק", value: failed ? "אין נתונים" : phase, does: "השלב לפי שעון הבורסה. סריקה לא רצה כשהשוק סגור." },
    ...rows.map((row) => ({ label: row.label, value: row.text, does: RULES[row.label] || row.text })),
    ...Object.entries(flags || {}).map(([key, ok]) => ({
      label: key,
      value: ok ? "PASS" : "FAIL",
      does: key === "STREAM_CONNECTED" ? "עובר רק אחרי בדיקת זרם אמיתית. מפתח שמור לבד אינו זרם." : "דגל מבדיקת Alpaca Paper. PASS רק אחרי קריאה שהצליחה.",
    })),
  ];
  const lines = [];
  for (let index = 0; index < cubes.length; index += 4) lines.push(cubes.slice(index, index + 4));

  return (
    <Shell>
      <section className="engine">
        <header className="ops-head">
          <div>
            <h1>בריאות המערכת</h1>
            <p>System Health · PAPER</p>
          </div>
          <span className={`ops-pip ${failed ? "off" : status ? "on" : "warn"}`}><i />{failed ? "אין נתונים" : status ? "נבדק" : "טוען"}</span>
        </header>
        <div className="engine-board">
          {lines.map((line, rowIndex) => (
            <div className="engine-row fit" key={line[0]?.label ?? rowIndex}>
              {line.map((item) => (
                <button key={item.label} type="button" className="engine-cube stat" onClick={() => setOpen({ title: item.label, does: item.does })}>
                  <span>{item.label}</span>
                  <b>{item.value}</b>
                </button>
              ))}
            </div>
          ))}
        </div>
        {open ? (
          <div className="engine-backdrop" onClick={() => setOpen(null)}>
            <article className="engine-modal" role="dialog" aria-modal="true" onClick={(event) => event.stopPropagation()}>
              <header>
                <div><p>בריאות · PAPER</p><h2>{open.title}</h2></div>
                <button type="button" onClick={() => setOpen(null)}>סגור</button>
              </header>
              <h3>מה הנתון</h3>
              <p>{open.does}</p>
              <h3>כלים</h3>
              <ul><li>בדיקת מערכת</li></ul>
              <h3>נוסחה</h3>
              <pre>{open.does}</pre>
            </article>
          </div>
        ) : null}
      </section>
    </Shell>
  );
}

function feedHealth(feed: SystemStatus["market_status"], count: number, name: string | undefined): { text: string; tone: Tone } {
  if (!name || name === "unconfigured") return { text: "אזהרה", tone: "warn" };
  if (!feed) return { text: "אין נתונים", tone: "wait" };
  if (feed.last_error) return { text: "תקלה", tone: "bad" };
  if (feed.last_success && count > 0) return { text: "תקין", tone: "ok" };
  if (feed.last_success) return { text: "אין ציטוט", tone: "wait" };
  return { text: "ממתין לטרמינל", tone: "wait" };
}

function newsHealth(status: SystemStatus): { text: string; tone: Tone } {
  const news = status.news_status;
  if (!news || status.providers.news === "unconfigured") return { text: "אזהרה", tone: "warn" };
  if (news.last_error) return { text: "תקלה", tone: "bad" };
  if (news.authenticated && news.last_success) return { text: "תקין", tone: "ok" };
  return { text: "מפתח שמור", tone: "wait" };
}

function healthRows(status: SystemStatus): { label: string; text: string; tone: Tone }[] {
  const ai = (): { text: string; tone: Tone } => {
    if (status.ai_mode === "stopped") return { text: "תקלה", tone: "bad" };
    if (status.ai_mode === "unpriced" || status.ai_mode === "soft" || status.ai_mode === "critical") return { text: "אזהרה", tone: "warn" };
    if (!status.ai_mode) return { text: "אין נתונים", tone: "wait" };
    return { text: "תקין", tone: "ok" };
  };
  const scanner = (): { text: string; tone: Tone } => {
    const age = status.worker_heartbeat_age_seconds;
    if (age === null || age === undefined) return { text: "אזהרה", tone: "warn" };
    if (age > 180) return { text: "אזהרה", tone: "warn" };
    return { text: "תקין", tone: "ok" };
  };
  const database = status.database === "ok" ? { text: "תקין", tone: "ok" as Tone } : status.database === "down" ? { text: "תקלה", tone: "bad" as Tone } : { text: "אין נתונים", tone: "wait" as Tone };
  const market = feedHealth(status.market_status, status.market_status?.quote_count ?? 0, status.providers.market);
  const news = newsHealth(status);
  const macro = status.macro_status;
  const fred = !macro || macro.provider === "unconfigured"
    ? { text: "אזהרה", tone: "warn" as Tone }
    : macro.last_error
      ? { text: "תקלה", tone: "bad" as Tone }
      : macro.authenticated && macro.last_success && macro.series_count > 0
        ? { text: "תקין", tone: "ok" as Tone }
        : { text: "מפתח שמור", tone: "wait" as Tone };
  const research = status.research_status;
  const sec = !research || research.provider === "unconfigured"
    ? { text: "אזהרה", tone: "warn" as Tone }
    : research.last_error
      ? { text: "תקלה", tone: "bad" as Tone }
      : research.authenticated && research.last_success && research.filing_count > 0
        ? { text: "תקין", tone: "ok" as Tone }
        : { text: "ממתין", tone: "wait" as Tone };
  return [
    { label: "Market Data", ...market },
    { label: "News", ...news },
    { label: "FRED", ...fred },
    { label: "SEC EDGAR", ...sec },
    { label: "AI", ...ai() },
    { label: "Supabase", text: "אין נתונים", tone: "wait" },
    { label: "מסד נתונים", ...database },
    { label: "Scanner", ...scanner() },
    { label: "Quant Engine", text: "אין נתונים", tone: "wait" },
    { label: "Paper Trading", text: status.paper_only ? "תקין" : "אין נתונים", tone: status.paper_only ? "ok" : "wait" },
  ];
}
