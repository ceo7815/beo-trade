"use client";

import { useEffect, useState } from "react";
import { Shell } from "@/components/Shell";
import { apiGet } from "@/lib/api";
import { money } from "@/lib/pnl";

type Fill = {
  execution_id?: string | null;
  symbol?: string | null;
  side?: string | null;
  qty?: string | null;
  price?: string | null;
  timestamp?: string | null;
};

type Finance = {
  today_pnl?: string | null;
  open_positions?: number | null;
  exposure?: string | null;
  trades?: { count?: number; enough?: boolean; net?: string | null; note?: string | null };
};

type Risk = { max_positions?: number | null };

type Explain = { title: string; does: string; tools: string[]; formula: string };

function asNumber(value: string | null | undefined) {
  if (value == null || value === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function dollars(value: string | null | undefined) {
  const parsed = asNumber(value);
  return parsed === null ? "אין נתונים" : money(parsed);
}

function sideName(side: string | null | undefined) {
  const value = (side || "").toLowerCase();
  if (value === "buy") return "כניסה";
  if (value === "sell") return "יציאה";
  return "אין נתונים";
}

export default function HistoryPage() {
  const [fills, setFills] = useState<Fill[] | null>(null);
  const [finance, setFinance] = useState<Finance | null>(null);
  const [risk, setRisk] = useState<Risk | null>(null);
  const [failed, setFailed] = useState(false);
  const [open, setOpen] = useState<Explain | null>(null);

  useEffect(() => {
    let alive = true;
    const load = () => {
      Promise.allSettled([
        apiGet<{ items: Fill[] }>("/api/v1/broker/alpaca/fills"),
        apiGet<Finance>("/api/v1/finance"),
        apiGet<Risk>("/api/v1/risk"),
      ]).then(([fillResult, financeResult, riskResult]) => {
        if (!alive) return;
        setFills(fillResult.status === "fulfilled" ? fillResult.value.items : null);
        setFinance(financeResult.status === "fulfilled" ? financeResult.value : null);
        setRisk(riskResult.status === "fulfilled" ? riskResult.value : null);
        setFailed(fillResult.status === "rejected");
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

  const buys = fills?.filter((row) => (row.side || "").toLowerCase() === "buy").length;
  const sells = fills?.filter((row) => (row.side || "").toLowerCase() === "sell").length;
  let contracts = 0;
  let contractsOk = fills !== null;
  for (const row of fills ?? []) {
    const qty = asNumber(row.qty);
    if (qty === null) contractsOk = false;
    else contracts += Math.abs(qty);
  }
  const maxPositions = risk?.max_positions;
  const openPositions = finance?.open_positions;
  const closed = finance?.trades?.count;

  const cubes: { label: string; value: string; explain: Explain }[] = [
    {
      label: "מילויים",
      value: fills ? String(fills.length) : "אין נתונים",
      explain: {
        title: "מילויים",
        does: "כל ביצוע שמולא אצל Alpaca Paper. רשימה ריקה היא אפס מילויים.",
        tools: ["Alpaca FILL"],
        formula: "מילויים = len(activities מסוג FILL)",
      },
    },
    {
      label: "כניסות",
      value: buys == null ? "אין נתונים" : String(buys),
      explain: {
        title: "כניסות",
        does: "מילויים שצד שלהם הוא קנייה. זה אותו ספר מילויים של Alpaca Paper.",
        tools: ["Alpaca FILL"],
        formula: "כניסות = count(side = buy)",
      },
    },
    {
      label: "יציאות",
      value: sells == null ? "אין נתונים" : String(sells),
      explain: {
        title: "יציאות",
        does: "מילויים שצד שלהם הוא מכירה. בלי מילוי יציאה אין עסקה סגורה.",
        tools: ["Alpaca FILL"],
        formula: "יציאות = count(side = sell)",
      },
    },
    {
      label: "חוזים",
      value: !fills || !contractsOk ? "אין נתונים" : String(contracts),
      explain: {
        title: "חוזים שמומשו",
        does: "סך החוזים בכל המילויים. חוזה בלי כמות משאיר את הסכום בלי נתון.",
        tools: ["Alpaca qty"],
        formula: "חוזים = Σ |qty|\nמכפיל = 100",
      },
    },
    {
      label: "רווח היום",
      value: dollars(finance?.today_pnl),
      explain: {
        title: "רווח היום",
        does: "אותו רווח יומי שמוצג בלוח הראשי ובבקרת הכספים. הוא ההפרש בין ההון להון הקודם.",
        tools: ["Alpaca equity", "last_equity"],
        formula: "היום = equity − last_equity",
      },
    },
    {
      label: "פוזיציות",
      value: openPositions == null ? "אין נתונים" : maxPositions == null ? String(openPositions) : `${openPositions} / ${maxPositions}`,
      explain: {
        title: "פוזיציות פתוחות",
        does: "אותו מספר פוזיציות פתוחות שמוצג במסך הפוזיציות.",
        tools: ["Alpaca positions", "trading.toml"],
        formula: "פתוח = len(positions)\nתקרה = max_concurrent_positions = 5",
      },
    },
    {
      label: "חשיפה",
      value: dollars(finance?.exposure),
      explain: {
        title: "חשיפה",
        does: "אותו שווי שוק פתוח שמוצג בפוזיציות ובבקרת הכספים.",
        tools: ["Alpaca market_value"],
        formula: "חשיפה = Σ |market_value|",
      },
    },
    {
      label: "נסגרו",
      value: closed == null ? "אין נתונים" : String(closed),
      explain: {
        title: "עסקאות סגורות",
        does: "עסקה נסגרת רק כשיש מילוי קנייה ומילוי מכירה עם מחיר. בלי שני הצדדים אין רווח עסקה.",
        tools: ["Alpaca FILL", "בקרת כספים"],
        formula: "ברוטו = (יציאה − כניסה) × כמות × 100\nנטו = ברוטו − עמלה רק כשהברוקר שלח עמלה\nבלי עמלה: עמלות טרם זמינות",
      },
    },
  ];
  const rows = [cubes.slice(0, 4), cubes.slice(4)];

  return (
    <Shell>
      <section className="engine">
        <header className="ops-head">
          <div>
            <h1>היסטוריית עסקאות</h1>
            <p>Trade History · PAPER</p>
          </div>
          <span className={`ops-pip ${failed ? "warn" : fills ? "on" : "warn"}`}>
            <i />
            {failed ? "אין נתונים" : fills ? "Alpaca Paper" : "טוען"}
          </span>
        </header>

        <div className="engine-board" aria-label="סיכום עסקאות">
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

        <div className="engine-stages" aria-label="מילויים">
          {fills && fills.length === 0 ? (
            <button
              type="button"
              className="engine-stage"
              onClick={() =>
                setOpen({
                  title: "מילויים",
                  does: "Alpaca Paper החזיר רשימת מילויים ריקה. אין עסקה שבוצעה בחשבון.",
                  tools: ["Alpaca FILL"],
                  formula: "מילויים = 0",
                })
              }
            >
              <span>Alpaca Paper</span>
              <b>אין מילויים</b>
            </button>
          ) : null}
          {fills?.map((fill, index) => (
            <button
              key={fill.execution_id || `${fill.symbol}-${index}`}
              type="button"
              className="engine-stage"
              style={{ animationDelay: `${index * 35}ms` }}
              onClick={() =>
                setOpen({
                  title: fill.symbol || "מילוי",
                  does: "ביצוע יחיד מחשבון Alpaca Paper.",
                  tools: ["Alpaca FILL"],
                  formula: `צד ${sideName(fill.side)}\nכמות ${fill.qty || "אין נתונים"}\nמחיר ${fill.price ? `$${fill.price}` : "אין נתונים"}\nזמן ${fill.timestamp || "אין נתונים"}\nמזהה ${fill.execution_id || "אין נתונים"}`,
                })
              }
            >
              <span className="num">{fill.symbol || "אין נתונים"} · {sideName(fill.side)}</span>
              <b className="num">{fill.price ? `$${fill.price}` : "אין נתונים"}</b>
            </button>
          ))}
          {failed && !fills ? <p className="ops-line">אין נתונים. השרת לא החזיר מילויים.</p> : null}
          {finance?.trades && finance.trades.enough === false ? <p className="ops-line">{finance.trades.note || "אין מספיק מידע לניתוח"}</p> : null}
        </div>
      </section>

      {open ? (
        <div className="engine-backdrop" onClick={() => setOpen(null)}>
          <article className="engine-modal" role="dialog" aria-modal="true" aria-labelledby="history-title" onClick={(event) => event.stopPropagation()}>
            <header>
              <div>
                <p>עסקאות · PAPER</p>
                <h2 id="history-title">{open.title}</h2>
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
