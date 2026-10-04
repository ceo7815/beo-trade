"use client";

import { useEffect, useState } from "react";
import { Shell } from "@/components/Shell";
import { apiGet, type SystemStatus } from "@/lib/api";

type Summary = {
  items?: number;
  latency_ms?: number | null;
  cache_hit_rate?: string | null;
  input_tokens?: number | null;
  output_tokens?: number | null;
};

type Explain = { title: string; does: string; tools: string[]; formula: string };

function money(value: string | null | undefined) {
  if (value == null || value === "") return "אין נתונים";
  const number = Number(value);
  return Number.isFinite(number) ? `$${number.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : "אין נתונים";
}

export default function AiPage() {
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [open, setOpen] = useState<Explain | null>(null);

  useEffect(() => {
    let alive = true;
    const load = () => {
      Promise.allSettled([
        apiGet<SystemStatus>("/api/v1/system"),
        apiGet<Summary>("/api/v1/ai/summary"),
      ]).then(([systemResult, summaryResult]) => {
        if (!alive) return;
        setStatus(systemResult.status === "fulfilled" ? systemResult.value : null);
        setSummary(summaryResult.status === "fulfilled" ? summaryResult.value : null);
      });
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

  const rate = summary?.cache_hit_rate == null ? "אין נתונים" : `${(Number(summary.cache_hit_rate) * 100).toFixed(0)}%`;
  const cubes: { label: string; value: string; explain: Explain }[] = [
    { label: "מודל", value: status?.ai_model || "אין נתונים", explain: { title: "מודל", does: "המודל היחיד שכותב החלטת BUY או SUPPRESS.", tools: ["OpenAI Responses"], formula: "מודל = openai_model\nפלט ≤ 800 טוקנים" } },
    { label: "מצב", value: status?.ai_mode || "אין נתונים", explain: { title: "מצב תקציב", does: "המצב נגזר מההוצאה מול התקרות. אין כאן שרשרת מחשבה.", tools: ["ai_request_logs"], formula: "תקרה חודשית = $200\nתקרה יומית = $20" } },
    { label: "עלות החודש", value: status ? `${money(status.ai_month_usd)} / ${money(status.ai_hard_limit)}` : "אין נתונים", explain: { title: "עלות החודש", does: "סכום העלות המחושבת החודש מול התקרה הקשיחה.", tools: ["AIUsage"], formula: "עלות = קלט × $2/M + מטמון × $0.10/M + פלט × $10/M" } },
    { label: "עלות היום", value: money(status?.ai_day_usd), explain: { title: "עלות היום", does: "אותה עלות יומית שמופיעה בלוח הראשי.", tools: ["AIUsage"], formula: "היום = Σ עלות מאז חצות UTC" } },
    { label: "שעה אחרונה", value: money(status?.ai_hour_usd), explain: { title: "עלות השעה", does: "עלות הקריאות בשעה האחרונה.", tools: ["AIUsage"], formula: "שעה = Σ עלות ב-3600 השניות האחרונות" } },
    { label: "קריאות", value: status ? String(status.ai_calls) : "אין נתונים", explain: { title: "קריאות החודש", does: "מספר קריאות ההחלטה שנשמרו החודש.", tools: ["AIUsage"], formula: "קריאות = count(קריאות החודש)\nלסריקה עד 8" } },
    { label: "מטמון", value: Number.isFinite(Number(summary?.cache_hit_rate)) ? rate : "אין נתונים", explain: { title: "פגיעות מטמון", does: "החלטה חוזרת בתוך ה-TTL לא קוראת למודל מחדש.", tools: ["fingerprint"], formula: "פגיעה = החלטות עם cache_hit / החלטות עם שדה\nTTL = 900 שניות" } },
    { label: "השהיה", value: summary?.latency_ms == null ? "אין נתונים" : `${summary.latency_ms} ms`, explain: { title: "השהיה ממוצעת", does: "ממוצע זמן הקריאה רק כשהשדה נשמר. בלי מדידה אין מספר.", tools: ["ai_request_logs"], formula: "השהיה = ממוצע latency_ms" } },
  ];
  const rows = [cubes.slice(0, 4), cubes.slice(4)];

  return (
    <Shell>
      <section className="engine">
        <header className="ops-head">
          <div>
            <h1>מרכז החלטות AI</h1>
            <p>AI Decision Center · PAPER</p>
          </div>
          <span className={`ops-pip ${status ? "on" : "warn"}`}><i />{status?.ai_mode || "טוען"}</span>
        </header>
        <div className="engine-board">
          {rows.map((row, index) => (
            <div className="engine-row fit" key={row[0]?.label ?? index}>
              {row.map((item) => (
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
                <div><p>החלטות AI · PAPER</p><h2>{open.title}</h2></div>
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
      </section>
    </Shell>
  );
}
