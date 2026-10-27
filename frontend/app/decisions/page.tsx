"use client";

import { useEffect, useState } from "react";
import { Shell } from "@/components/Shell";
import { apiGet, poll } from "@/lib/api";
import { blockReasons, copy } from "@/lib/copy";

type Decision = {
  id: string;
  decision: string;
  underlying: string;
  option_symbol: string;
  call_put: string;
  strike: string;
  expiration: string;
  thesis: string;
  catalyst: string;
  risk: string;
  invalidation: string;
  suppress_reason: string;
  reason_codes: string[];
  created_at: string | null;
};

const WEEKDAYS = ["ראשון", "שני", "שלישי", "רביעי", "חמישי", "שישי", "שבת"];

const REASONS = blockReasons;

function why(item: Decision) {
  if (item.decision === "BUY") return "עבר את השערים";
  const key = item.suppress_reason || "";
  return REASONS[key] || key || "נדחה";
}

function strike(value: string) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return value || "";
  return parsed.toLocaleString("en-US", { maximumFractionDigits: 2 });
}

function clock(iso: string | null) {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  return new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "Asia/Dubai" }).format(date);
}

function nyDay(iso: string | null) {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  return new Intl.DateTimeFormat("en-CA", { timeZone: "America/New_York", year: "numeric", month: "2-digit", day: "2-digit" }).format(date);
}

function dayTitle(day: string) {
  const [year, month, date] = day.split("-").map(Number);
  if (!year || !month || !date) return day;
  const weekday = WEEKDAYS[new Date(Date.UTC(year, month - 1, date)).getUTCDay()] ?? "";
  return `${weekday} ${String(date).padStart(2, "0")}/${String(month).padStart(2, "0")}`;
}

function daysOf(items: Decision[]) {
  const groups = new Map<string, { day: string; count: number; buys: number }>();
  for (const item of items) {
    const day = nyDay(item.created_at);
    if (!day) continue;
    const group = groups.get(day) ?? { day, count: 0, buys: 0 };
    group.count += 1;
    if (item.decision === "BUY") group.buys += 1;
    groups.set(day, group);
  }
  return [...groups.values()].sort((a, b) => (a.day < b.day ? 1 : -1));
}

export default function DecisionsPage() {
  const [items, setItems] = useState<Decision[] | null>(null);
  const [failed, setFailed] = useState(false);
  const [choice, setChoice] = useState<string | null>(null);
  const [side, setSide] = useState<"all" | "BUY" | "SUPPRESS">("all");
  const [menu, setMenu] = useState(false);
  const [picked, setPicked] = useState<Decision | null>(null);
  const [aiCost, setAiCost] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    const load = () => {
      const system = apiGet<{ ai_month_usd?: string | number; ai_hard_limit?: string | number }>("/api/v1/system")
        .then((body) => {
          if (!alive) return;
          const month = body.ai_month_usd;
          const cap = body.ai_hard_limit;
          setAiCost(month == null ? null : `$${month}${cap == null ? "" : ` / $${cap}`}`);
        })
        .catch(() => alive && setAiCost(null));
      const rows = apiGet<{ items: Decision[] }>("/api/v1/decisions?limit=200")
        .then((body) => {
          if (!alive) return;
          setItems(body.items);
          setFailed(false);
        })
        .catch(() => alive && setFailed(true));
      return Promise.all([system, rows]);
    };
    const stop = poll(() => load(), 30000);
    return () => {
      alive = false;
      stop();
    };
  }, []);

  useEffect(() => {
    if (!picked && !menu) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      if (picked) setPicked(null);
      else setMenu(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [picked, menu]);

  const rows = items ?? [];
  const days = daysOf(rows);
  const showingAll = choice === "all";
  const active = showingAll ? "" : (choice ?? days[0]?.day ?? "");
  const dayRows = showingAll ? rows : rows.filter((item) => nyDay(item.created_at) === active);
  const shown = dayRows
    .filter((item) => side === "all" || item.decision === side)
    .sort((a, b) => new Date(b.created_at ?? 0).getTime() - new Date(a.created_at ?? 0).getTime());
  const buys = dayRows.filter((item) => item.decision === "BUY").length;
  const nos = dayRows.length - buys;
  const selected = showingAll ? null : days.find((group) => group.day === active);
  const label = !items ? "טוען" : days.length === 0 ? "אין החלטות" : showingAll ? "כל הימים" : (dayTitle(active) || "אין החלטות");

  const pickDay = (next: string) => {
    setChoice(next);
    setMenu(false);
  };

  return (
    <Shell>
      <section className="engine book">
        <header className="ops-head">
          <div>
            <h1>{copy.screens.decisions}</h1>
            <p>200 ההחלטות האחרונות</p>
          </div>
          <span className={`ops-pip ${failed || items === null ? "warn" : "on"}`}>
            <i />
            {failed ? "אין נתונים" : items ? "PAPER" : "טוען"}
          </span>
        </header>
        <p className="ops-line">עלות AI החודש {aiCost ?? "—"}</p>

        <div className="book-pick">
          <button type="button" className="book-trigger" aria-expanded={menu} aria-haspopup="listbox" onClick={() => setMenu((value) => !value)}>
            <span>
              <small>יום מסחר</small>
              {label}
            </span>
            <b className="decision-count">{items ? <><span className="verdict yes">{buys} כן</span><span className="verdict no">{nos} לא</span></> : "—"}</b>
          </button>
          {menu ? (
            <div className="book-menu" role="listbox" aria-label="ימי מסחר">
              <button type="button" className={`book-option${showingAll ? " on" : ""}`} onClick={() => pickDay("all")}>
                <span>כל הימים<small>{rows.length} החלטות</small></span>
                <b className="decision-count"><span className="verdict yes">{rows.filter((item) => item.decision === "BUY").length} כן</span><span className="verdict no">{rows.length - rows.filter((item) => item.decision === "BUY").length} לא</span></b>
              </button>
              {days.map((group) => (
                <button key={group.day} type="button" className={`book-option${active === group.day ? " on" : ""}`} onClick={() => pickDay(group.day)}>
                  <span>{dayTitle(group.day)}<small>{group.count} החלטות</small></span>
                  <b className="decision-count"><span className="verdict yes">{group.buys} כן</span><span className="verdict no">{group.count - group.buys} לא</span></b>
                </button>
              ))}
            </div>
          ) : null}
        </div>
        {menu ? <button type="button" className="book-shade" aria-label="סגור תאריכים" onClick={() => setMenu(false)} /> : null}

        <div className="choice-row" role="tablist" aria-label="סוג החלטה">
          {([
            ["all", "הכל", dayRows.length],
            ["BUY", "כן", buys],
            ["SUPPRESS", "לא", nos],
          ] as const).map(([id, name, count]) => (
            <button key={id} type="button" role="tab" aria-selected={side === id} className={`choice${side === id ? " on" : ""}`} onClick={() => setSide(id)}>
              {name}
              <b className="num">{items ? count : "—"}</b>
            </button>
          ))}
        </div>

        <section className="desk-days">
          <div className="desk-blotter">
            {shown.length === 0 ? (
              <p className="desk-empty">{!items ? "טוען" : items.length === 0 ? "אין החלטות שמורות בעותק הזה" : "אין החלטה בסינון הזה"}</p>
            ) : shown.map((item, index) => {
              const yes = item.decision === "BUY";
              const day = nyDay(item.created_at);
              const previous = index > 0 ? nyDay(shown[index - 1].created_at) : "";
              return (
                <div key={item.id}>
                  {showingAll && day !== previous ? (
                    <div className="book-day">
                      <b>{dayTitle(day) || "תאריך לא ידוע"}</b>
                    </div>
                  ) : null}
                  <button type="button" className="decision-row" onClick={() => setPicked(item)}>
                    <strong className={yes ? "verdict yes" : "verdict no"}>{yes ? "כן" : "לא"}</strong>
                    <b>{item.underlying || "—"} <small>{item.call_put} {strike(item.strike)}</small></b>
                    <span><em className="num">{clock(item.created_at)}</em>{why(item)}</span>
                  </button>
                </div>
              );
            })}
          </div>
        </section>
      </section>

      {picked ? (
        <div className="engine-backdrop" onClick={() => setPicked(null)}>
          <article className="engine-modal" role="dialog" aria-modal="true" onClick={(event) => event.stopPropagation()}>
            <header>
              <div>
                <p>{picked.decision === "BUY" ? "כן" : "לא"} · {clock(picked.created_at)} · PAPER</p>
                <h2 className={picked.decision === "BUY" ? "up" : "down"}>{picked.underlying} {picked.call_put} {strike(picked.strike)}</h2>
              </div>
              <button type="button" onClick={() => setPicked(null)}>סגור</button>
            </header>
            <p className="book-why">{why(picked)}</p>
            {picked.thesis ? <p>{picked.thesis}</p> : null}
            {picked.catalyst ? <p>{picked.catalyst}</p> : null}
            {picked.risk ? <p>{picked.risk}</p> : null}
            {picked.invalidation ? <p>{picked.invalidation}</p> : null}
          </article>
        </div>
      ) : null}
    </Shell>
  );
}
