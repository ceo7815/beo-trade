"use client";

import { useEffect, useState } from "react";
import { Shell } from "@/components/Shell";
import { apiGet } from "@/lib/api";

type Row = { trade_id: string; state: string; source: string; reason: string; created_at: string };
type Explain = { title: string; does: string; formula: string };

export default function AuditPage() {
  const [items, setItems] = useState<Row[] | null>(null);
  const [open, setOpen] = useState<Explain | null>(null);

  useEffect(() => {
    let alive = true;
    const load = () => apiGet<{ items: Row[] }>("/api/v1/audit").then((body) => alive && setItems(body.items)).catch(() => alive && setItems(null));
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

  const states = new Set((items || []).map((item) => item.state));
  const cubes = [
    { label: "רשומות", value: items ? String(items.length) : "אין נתונים", explain: { title: "רשומות ביקורת", does: items && items.length === 0 ? "אין עדיין רשומות ביקורת. אירוע נשמר רק אחרי מעבר מצב של פקודה." : "אירועי מצב של פקודות PAPER. אין כאן שרשרת מחשבה.", formula: "רשומה = מצב + מקור + סיבה + זמן\nמוצגות עד 40 אחרונות" } },
    { label: "מצבים", value: items ? String(states.size) : "אין נתונים", explain: { title: "מצבים", does: "כמה מצבים שונים נשמרו ברשימה הנוכחית.", formula: "מצבים = unique(state)" } },
  ];
  const events = (items || []).map((item, index) => ({
    label: item.state || "אין נתונים",
    value: item.reason || item.source || "אין נתונים",
    key: `${item.trade_id}-${index}`,
    explain: { title: item.state || "אירוע", does: "מעבר מצב שנשמר אחרי בדיקה, שליחה או מילוי.", formula: `מקור ${item.source || "אין נתונים"}\nסיבה ${item.reason || "אין נתונים"}\nזמן ${item.created_at || "אין נתונים"}\nמזהה ${item.trade_id || "אין נתונים"}` },
  }));
  const lines = [];
  const all = [...cubes.map((item, index) => ({ ...item, key: `sum-${index}` })), ...events];
  for (let index = 0; index < all.length; index += 4) lines.push(all.slice(index, index + 4));

  return (
    <Shell>
      <section className="engine">
        <header className="ops-head">
          <div>
            <h1>ביקורת מערכת</h1>
            <p>Audit · PAPER</p>
          </div>
          <span className={`ops-pip ${items ? "on" : "warn"}`}><i />{items ? "המסד" : "טוען"}</span>
        </header>
        <div className="engine-board">
          {lines.map((line, rowIndex) => (
            <div className="engine-row fit" key={line[0]?.key ?? rowIndex}>
              {line.map((item) => (
                <button key={item.key} type="button" className="engine-cube" onClick={() => setOpen(item.explain)}>
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
                <div><p>ביקורת · PAPER</p><h2>{open.title}</h2></div>
                <button type="button" onClick={() => setOpen(null)}>סגור</button>
              </header>
              <h3>מה הנתון</h3>
              <p>{open.does}</p>
              <h3>כלים</h3>
              <ul><li>broker_trade_events</li></ul>
              <h3>נוסחה</h3>
              <pre>{open.formula}</pre>
            </article>
          </div>
        ) : null}
      </section>
    </Shell>
  );
}
