"use client";

import { useEffect, useState } from "react";
import { Shell } from "@/components/Shell";
import { apiGet } from "@/lib/api";

type SettingsView = {
  paper_only: boolean;
  model: string;
  display_timezone: string;
  exchange_timezone: string;
  thresholds: Record<string, number | string>;
};

type Explain = { title: string; does: string; formula: string };

const COPY: Record<string, Explain> = {
  min_option_volume: { title: "נפח אופציה", does: "חוזה נפסל אם המחזור מתחת לסף.", formula: "מחזור ≥ min_option_volume" },
  min_open_interest: { title: "Open Interest", does: "חוזה נפסל אם ה-OI מתחת לסף.", formula: "OI ≥ min_open_interest" },
  max_bid_ask_spread: { title: "מרווח מקסימלי", does: "חוזה נפסל אם המרווח היחסי גדול מהסף.", formula: "spread = (ask − bid) / mid ≤ הסף" },
  max_expiration_days: { title: "ימים לפקיעה", does: "חוזה נפסל אם ה-DTE מחוץ לחלון.", formula: "0 ≤ DTE ≤ max_expiration_days" },
  min_delta: { title: "דלתא מינימלית", does: "הדלתא בערך מוחלט חייבת להיות לפחות הסף.", formula: "|delta| ≥ min_delta" },
  max_delta: { title: "דלתא מקסימלית", does: "הדלתא בערך מוחלט חייבת להיות לכל היותר הסף.", formula: "|delta| ≤ max_delta" },
  max_buys_per_scan: { title: "מקסימום BUY", does: "תקרת החלטות BUY בסריקה אחת.", formula: "BUY בסריקה ≤ max_buys_per_scan" },
  scan_interval_seconds: { title: "מרווח סריקה", does: "המנוע נחשב פעיל אם גיל הדופק בתוך שלושה מרווחים.", formula: "פעיל אם גיל ≤ מרווח × 3" },
  holding_window_min_minutes: { title: "חלון מינימלי", does: "רצפת זמן ההחזקה בדקות.", formula: "חלון מינימלי = holding_window_min_minutes" },
  holding_window_max_minutes: { title: "חלון מקסימלי", does: "זמן ההחזקה נמדד מהמילוי. 0DTE ו-1DTE נסגרים לפי הדקות שבמדיניות.", formula: "0DTE ≤ 90 דקות\n1DTE ≤ 180 דקות" },
  max_capital_per_trade: { title: "תקרת דולר ישנה", does: "השדה אינו קובע כמות. הכמות החיה באה ממדיניות AGGRESSIVE.", formula: "כמות = MIN(סיכון 2%, הון 7%, תקרה 10%, חשיפה 35%, סקטור 10%, סיכון מצטבר 15%, Buying Power)" },
};

export default function SettingsPage() {
  const [settings, setSettings] = useState<SettingsView | null>(null);
  const [open, setOpen] = useState<Explain | null>(null);

  useEffect(() => {
    apiGet<SettingsView>("/api/v1/settings").then(setSettings).catch(() => setSettings(null));
  }, []);

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  const head = settings
    ? [
        { label: "מודל", value: settings.model, explain: { title: "מודל", does: "המודל שכותב את החלטת ה-AI.", formula: "מודל = openai_model" } },
        { label: "תצוגה", value: settings.display_timezone, explain: { title: "אזור תצוגה", does: "השעות על המסך.", formula: "תצוגה = Asia/Dubai" } },
        { label: "בורסה", value: settings.exchange_timezone, explain: { title: "אזור בורסה", does: "הזמן הפנימי של הסשן.", formula: "בורסה = America/New_York" } },
        { label: "מצב", value: settings.paper_only ? "PAPER" : "אין נתונים", explain: { title: "מצב מסחר", does: "המערכת רצה על חשבון Alpaca Paper.", formula: "TRADING_MODE = PAPER" } },
      ]
    : [];
  const thresholds = Object.entries(settings?.thresholds || {})
    .filter(([key]) => key !== "paper_account_balance")
    .map(([key, value]) => ({
      label: COPY[key]?.title || key,
      value: String(value),
      explain: COPY[key] || { title: key, does: "סף מקובץ ההגדרות.", formula: key },
    }));
  const cubes = [...head, ...thresholds];
  const lines = [];
  for (let index = 0; index < cubes.length; index += 4) lines.push(cubes.slice(index, index + 4));

  return (
    <Shell>
      <section className="engine">
        <header className="ops-head">
          <div>
            <h1>הגדרות</h1>
            <p>Settings · PAPER</p>
          </div>
          <span className={`ops-pip ${settings ? "on" : "warn"}`}><i />{settings ? "קובץ ההגדרות" : "טוען"}</span>
        </header>
        <div className="engine-board">
          {lines.map((line, rowIndex) => (
            <div className="engine-row fit" key={line[0]?.label ?? rowIndex}>
              {line.map((item) => (
                <button key={item.label} type="button" className="engine-cube stat" onClick={() => setOpen(item.explain)}>
                  <span>{item.label}</span>
                  <b className="num">{item.value}</b>
                </button>
              ))}
            </div>
          ))}
        </div>
        {open ? (
          <div className="engine-backdrop" onClick={() => setOpen(null)}>
            <article className="engine-modal" role="dialog" aria-modal="true" onClick={(event) => event.stopPropagation()}>
              <header>
                <div><p>הגדרות · PAPER</p><h2>{open.title}</h2></div>
                <button type="button" onClick={() => setOpen(null)}>סגור</button>
              </header>
              <h3>מה הנתון</h3>
              <p>{open.does}</p>
              <h3>כלים</h3>
              <ul><li>trading.toml</li></ul>
              <h3>נוסחה</h3>
              <pre>{open.formula}</pre>
            </article>
          </div>
        ) : null}
      </section>
    </Shell>
  );
}
