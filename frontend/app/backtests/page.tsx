"use client";

import { useEffect, useState } from "react";
import { Shell } from "@/components/Shell";

type Explain = { title: string; does: string; formula: string };

const CUBES: { label: string; value: string; explain: Explain }[] = [
  { label: "צילומים", value: "אין נתונים", explain: { title: "צילומי שוק", does: "בדיקת עבר דורשת צילומי שרשרת שנשמרו בזמן אמת. אין עדיין היסטוריה כזאת.", formula: "צילום = ציטוט + שרשרת + זמן\nבלי צילום אין בדיקה" } },
  { label: "עסקאות", value: "אין נתונים", explain: { title: "עסקאות היסטוריות", does: "אין עסקאות סגורות עם מחיר כניסה ומחיר יציאה לבדיקה.", formula: "עסקה = כניסה + יציאה + כמות" } },
  { label: "תוצאה", value: "אין נתונים", explain: { title: "תוצאת בדיקה", does: "אין תוצאה כי הבדיקה לא רצה. אין כאן אחוז מומצא.", formula: "תוצאה = Σ רווח העסקאות בבדיקה" } },
  { label: "חלון", value: "אין נתונים", explain: { title: "חלון זמן", does: "אין טווח תאריכים עם צילומים שמורים.", formula: "חלון = מתאריך עד תאריך על צילומים שמורים" } },
];

export default function BacktestsPage() {
  const [open, setOpen] = useState<Explain | null>(null);
  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  return (
    <Shell>
      <section className="engine">
        <header className="ops-head">
          <div>
            <h1>בדיקות עבר</h1>
            <p>Backtesting · PAPER</p>
          </div>
          <span className="ops-pip warn"><i />אין היסטוריה</span>
        </header>
        <div className="engine-row fit">
          {CUBES.map((item) => (
            <button key={item.label} type="button" className="engine-cube stat" onClick={() => setOpen(item.explain)}>
              <span>{item.label}</span>
              <b>{item.value}</b>
            </button>
          ))}
        </div>
        {open ? (
          <div className="engine-backdrop" onClick={() => setOpen(null)}>
            <article className="engine-modal" role="dialog" aria-modal="true" onClick={(event) => event.stopPropagation()}>
              <header>
                <div><p>בדיקות עבר · PAPER</p><h2>{open.title}</h2></div>
                <button type="button" onClick={() => setOpen(null)}>סגור</button>
              </header>
              <h3>מה הנתון</h3>
              <p>{open.does}</p>
              <h3>כלים</h3>
              <ul><li>צילומי שוק שמורים</li></ul>
              <h3>נוסחה</h3>
              <pre>{open.formula}</pre>
            </article>
          </div>
        ) : null}
      </section>
    </Shell>
  );
}
