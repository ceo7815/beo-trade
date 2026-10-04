"use client";

import { useEffect, useState } from "react";
import { Shell } from "@/components/Shell";
import { apiGet, type SystemStatus } from "@/lib/api";

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

type Desk = {
  last_scan: {
    observed_at?: string;
    recommendations?: number;
    buys?: number;
    ai_calls?: number;
    universe?: { discovered?: number; filtered?: number } | null;
  } | null;
};

type Explain = { title: string; does: string; tools: string[]; formula: string };

function text(value: string | null | undefined) {
  return value ? value : "אין נתונים";
}

function countText(value: number | null | undefined) {
  return value == null ? "אין נתונים" : String(value);
}

export default function DecisionsPage() {
  const [items, setItems] = useState<Decision[] | null>(null);
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [desk, setDesk] = useState<Desk | null>(null);
  const [open, setOpen] = useState<Explain | null>(null);

  useEffect(() => {
    let alive = true;
    const load = () => {
      Promise.allSettled([
        apiGet<{ items: Decision[] }>("/api/v1/decisions?limit=200"),
        apiGet<SystemStatus>("/api/v1/system"),
        apiGet<Desk>("/api/v1/desk"),
      ]).then(([decisionResult, statusResult, deskResult]) => {
        if (!alive) return;
        setItems(decisionResult.status === "fulfilled" ? decisionResult.value.items : null);
        setStatus(statusResult.status === "fulfilled" ? statusResult.value : null);
        setDesk(deskResult.status === "fulfilled" ? deskResult.value : null);
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

  const stored = status ? status.internal_candidates : null;
  const buys = status ? status.active_buys : null;
  const suppressed = stored == null || buys == null ? null : stored - buys;
  const scan = desk?.last_scan;

  const cubes: { label: string; value: string; explain: Explain }[] = [
    {
      label: "המלצות",
      value: countText(stored),
      explain: {
        title: "המלצות שמורות",
        does: "כל ההחלטות שנשמרו במסד. אותו מספר שמופיע בלוח הראשי כהמלצות שמורות.",
        tools: ["RecommendationRow"],
        formula: "המלצות = count(RecommendationRow)",
      },
    },
    {
      label: "BUY",
      value: countText(buys),
      explain: {
        title: "החלטות BUY שמורות",
        does: "החלטות שהמנוע שמר כ-BUY. אותו מספר שמופיע בלוח הראשי.",
        tools: ["RecommendationRow"],
        formula: "BUY = count(decision = BUY)",
      },
    },
    {
      label: "דיכוי",
      value: countText(suppressed),
      explain: {
        title: "דיכוי",
        does: "החלטה שאינה BUY. המודל והשערים מחזירים SUPPRESS כשאין מספיק ראיות.",
        tools: ["RecommendationRow", "שערי איכות"],
        formula: "דיכוי = המלצות שמורות − BUY\nערך שאינו BUY או SUPPRESS נשמר כ-SUPPRESS",
      },
    },
    {
      label: "אחרונות",
      value: items ? String(items.length) : "אין נתונים",
      explain: {
        title: "החלטות אחרונות",
        does: "השורות שנטענו למסך הזה, מהחדשה לישנה.",
        tools: ["היסטוריית החלטות"],
        formula: "אחרונות = min(המלצות שמורות, 200)",
      },
    },
    {
      label: "סוננו",
      value: countText(scan?.universe?.filtered),
      explain: {
        title: "סוננו בסריקה האחרונה",
        does: "אותו מונה יקום שמופיע במערכת האוטונומית. בלי סריקה שמורה אין מספר.",
        tools: ["last_scan", "Alpaca Assets"],
        formula: "סוננו = universe.filtered\nפלח = 40 · רענון = 21600 שניות",
      },
    },
    {
      label: "החלטות סריקה",
      value: countText(scan?.recommendations),
      explain: {
        title: "החלטות בסריקה האחרונה",
        does: "כמה החלטות נוצרו בסריקה האחרונה. אותו שדה שמופיע במערכת האוטונומית.",
        tools: ["last_scan"],
        formula: "החלטות = last_scan.recommendations",
      },
    },
    {
      label: "קריאות AI",
      value: countText(scan?.ai_calls),
      explain: {
        title: "קריאות AI בסריקה",
        does: "קריאות המודל בסריקה האחרונה. התקרה היא 8 קריאות לסריקה.",
        tools: ["OpenAI Responses", "last_scan"],
        formula: "קריאות = last_scan.ai_calls\nתקרה = 8 לסריקה\nמעבר לתקרה: SUPPRESS · AI_BUDGET_EXCEEDED",
      },
    },
    {
      label: "ביצועים",
      value: countText(scan?.buys),
      explain: {
        title: "ביצועים בסריקה",
        does: "החלטות BUY בסריקה האחרונה. אותו מונה שמופיע במערכת האוטונומית. BUY שמור אינו מילוי אצל הברוקר.",
        tools: ["last_scan", "Alpaca Paper"],
        formula: "ביצועים = count(decision = BUY) בסריקה האחרונה",
      },
    },
  ];
  const rows = [cubes.slice(0, 4), cubes.slice(4)];

  return (
    <Shell>
      <section className="engine">
        <header className="ops-head">
          <div>
            <h1>היסטוריית החלטות</h1>
            <p>Decision History · PAPER</p>
          </div>
          <span className={`ops-pip ${items ? "on" : "warn"}`}>
            <i />
            {items ? "המסד" : "טוען"}
          </span>
        </header>

        <div className="engine-board" aria-label="סיכום החלטות">
          {rows.map((row, rowIndex) => (
            <div className="engine-row fit" key={row[0]?.label ?? rowIndex}>
              {row.map((cube, index) => (
                <button
                  key={cube.label}
                  type="button"
                  className="engine-cube stat"
                  style={{ animationDelay: `${(rowIndex * 4 + index) * 40}ms` }}
                  onClick={() => setOpen(cube.explain)}
                >
                  <span>{cube.label}</span>
                  <b className="num">{cube.value}</b>
                </button>
              ))}
            </div>
          ))}
        </div>

        <div className="engine-stages" aria-label="החלטות">
          {items && items.length === 0 ? (
            <button
              type="button"
              className="engine-stage"
              onClick={() =>
                setOpen({
                  title: "היסטוריית החלטות",
                  does: "אין עדיין החלטה שמורה. סריקה לא רצה כל עוד השוק סגור, ולכן אין שורות.",
                  tools: ["RecommendationRow", "last_scan"],
                  formula: "המלצות שמורות = 0",
                })
              }
            >
              <span>המסד</span>
              <b>אין עדיין היסטוריה מספקת</b>
            </button>
          ) : null}
          {items?.map((item, index) => (
            <button
              key={item.id}
              type="button"
              className="engine-stage"
              style={{ animationDelay: `${index * 35}ms` }}
              onClick={() =>
                setOpen({
                  title: `${item.decision} · ${item.underlying || "אין נתונים"}`,
                  does: "החלטה שמורה. התזה, הזרז והביטול הם מה שנשמר. אין כאן שרשרת מחשבה של המודל.",
                  tools: ["RecommendationRow", item.decision === "BUY" ? "שערי איכות" : "SUPPRESS"],
                  formula: [
                    `החלטה ${text(item.decision)}`,
                    `נכס ${text(item.underlying)} ${text(item.call_put)} ${text(item.strike)}`,
                    `חוזה ${text(item.option_symbol)}`,
                    `פקיעה ${text(item.expiration)}`,
                    `תזה ${text(item.thesis)}`,
                    `זרז ${text(item.catalyst)}`,
                    `סיכון ${text(item.risk)}`,
                    `ביטול ${text(item.invalidation)}`,
                    `סיבה ${text(item.suppress_reason)}`,
                    `קודים ${item.reason_codes?.length ? item.reason_codes.join(", ") : "אין נתונים"}`,
                    `זמן ${text(item.created_at)}`,
                  ].join("\n"),
                })
              }
            >
              <span className="num">{item.decision} · {item.underlying || "אין נתונים"} {item.call_put} {item.strike}</span>
              <b>{item.suppress_reason || item.created_at || "אין נתונים"}</b>
            </button>
          ))}
          {!scan ? <p className="ops-line">אין עדיין היסטוריית סריקה. מוני הסריקה זהים למערכת האוטונומית.</p> : null}
        </div>
      </section>

      {open ? (
        <div className="engine-backdrop" onClick={() => setOpen(null)}>
          <article className="engine-modal" role="dialog" aria-modal="true" aria-labelledby="decision-title" onClick={(event) => event.stopPropagation()}>
            <header>
              <div>
                <p>החלטות · PAPER</p>
                <h2 id="decision-title">{open.title}</h2>
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
