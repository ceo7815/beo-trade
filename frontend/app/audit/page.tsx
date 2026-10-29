"use client";

import { useEffect, useState } from "react";
import { Shell } from "@/components/Shell";
import { blockText, copy } from "@/lib/copy";
import { apiGet, poll } from "@/lib/api";
import { sessionStamp } from "@/lib/format";

type Row = { trade_id: string; state: string; source: string; reason: string; created_at: string };
type AlertRow = { id: string; kind: string; message: string; created_at: string };
type Explain = { title: string; does: string; formula: string };
type Line = { key: string; label: string; value: string; when?: string; explain: Explain };

export default function AuditPage() {
  const [items, setItems] = useState<Row[] | null>(null);
  const [alerts, setAlerts] = useState<AlertRow[] | null>(null);
  const [open, setOpen] = useState<Explain | null>(null);

  useEffect(() => {
    let alive = true;
    const load = () => {
      const audit = apiGet<{ items: Row[] }>("/api/v1/audit").then((body) => alive && setItems(body.items)).catch(() => alive && setItems(null));
      const notes = apiGet<{ items: AlertRow[] }>("/api/v1/alerts").then((body) => alive && setAlerts(body.items)).catch(() => alive && setAlerts(null));
      return Promise.all([audit, notes]);
    };
    const stop = poll(() => load(), 30000);
    return () => {
      alive = false;
      stop();
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
  const cubes: Line[] = [
    { key: "sum-rows", label: "רשומות", value: items ? String(items.length) : "אין נתונים", explain: { title: "רשומות ביקורת", does: items && items.length === 0 ? "אין עדיין רשומות ביקורת. אירוע נשמר רק אחרי מעבר מצב של פקודה." : "אירועי מצב של פקודות PAPER. אין כאן שרשרת מחשבה.", formula: "רשומה = מצב + מקור + סיבה + זמן\nמוצגות עד 40 אחרונות" } },
    { key: "sum-states", label: "מצבים", value: items ? String(states.size) : "אין נתונים", explain: { title: "מצבים", does: "כמה מצבים שונים נשמרו ברשימה הנוכחית.", formula: "מצבים = unique(state)" } },
  ];
  const notes: Line[] = (alerts || []).map((item) => ({
    key: item.id,
    label: item.kind || "התראה",
    value: item.message || "אין נתונים",
    when: sessionStamp(item.created_at, true),
    explain: { title: item.kind || "התראה", does: item.message || "התראת BUY.", formula: `דובאי ${sessionStamp(item.created_at, true)}` },
  }));
  const events: Line[] = (items || []).map((item, index) => ({
    key: `${item.trade_id}-${index}`,
    label: item.state || "אין נתונים",
    value: item.reason ? blockText(item.reason) : item.source || "אין נתונים",
    when: sessionStamp(item.created_at, true),
    explain: { title: item.state || "אירוע", does: "מעבר מצב שנשמר אחרי בדיקה, שליחה או מילוי.", formula: `מקור ${item.source || "אין נתונים"}\nסיבה ${item.reason ? blockText(item.reason) : "אין נתונים"}\nדובאי ${sessionStamp(item.created_at, true)}\nמזהה ${item.trade_id || "אין נתונים"}` },
  }));
  const all = [...cubes, ...notes, ...events];

  return (
    <Shell>
      <section className="engine">
        <header className="ops-head">
          <div>
            <h1>{copy.screens.audit}</h1>
            <p>Audit · PAPER</p>
          </div>
          <span className={`ops-pip ${items ? "on" : "warn"}`}><i />{items ? "המסד" : "טוען"}</span>
        </header>
        <div className="plain-list">
          {all.map((item) => (
            <button key={item.key} type="button" className="plain-row" onClick={() => setOpen(item.explain)}>
              <b>{item.label}{item.when ? <small>{item.when}</small> : null}</b>
              <strong>{item.value}</strong>
            </button>
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
