"use client";

import { useEffect, useState } from "react";
import { Shell } from "@/components/Shell";
import { apiGet } from "@/lib/api";
import { money, percent } from "@/lib/pnl";

type BrokerPosition = {
  symbol?: string;
  underlying?: string;
  right?: string;
  strike?: string;
  expiration?: string;
  qty?: string;
  avg_entry_price?: string;
  current_price?: string;
  market_value?: string;
  cost_basis?: string;
  unrealized_pl?: string;
  unrealized_plpc?: string;
  asset_class?: string;
};

type BrokerOrder = {
  symbol?: string;
  position_intent?: string;
  internal_state?: string;
};

type Risk = {
  max_positions?: number | null;
  exposure_limit?: string | null;
  per_trade_limit?: string | null;
  state?: string;
};

type Explain = {
  title: string;
  does: string;
  tools: string[];
  formula: string;
};

function asNumber(value: string | undefined) {
  if (value === undefined || value === null || value === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function shown(value: string | number | null | undefined, prefix = "") {
  if (value === undefined || value === null || value === "") return "אין נתונים";
  return `${prefix}${value}`;
}

function dte(expiration: string | undefined) {
  if (!expiration) return null;
  const end = Date.parse(`${expiration}T00:00:00Z`);
  if (!Number.isFinite(end)) return null;
  const now = new Date();
  const today = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate());
  return Math.round((end - today) / 86400000);
}

function book(items: BrokerPosition[] | null) {
  if (!items) return null;
  let contracts = 0;
  let contractsOk = true;
  let exposure = 0;
  let exposureOk = true;
  let cost = 0;
  let costOk = true;
  let pnl = 0;
  let pnlOk = true;
  for (const item of items) {
    const qty = asNumber(item.qty);
    if (qty === null) contractsOk = false;
    else contracts += Math.abs(qty);
    const value = asNumber(item.market_value);
    if (value === null) exposureOk = false;
    else exposure += Math.abs(value);
    const basis = asNumber(item.cost_basis);
    if (basis === null) costOk = false;
    else cost += Math.abs(basis);
    const row = asNumber(item.unrealized_pl);
    if (!item.current_price || row === null) pnlOk = false;
    else pnl += row;
  }
  if (items.length === 0) {
    contractsOk = true;
    exposureOk = true;
    costOk = true;
    pnlOk = true;
  }
  return {
    count: items.length,
    calls: items.filter((item) => item.right === "CALL").length,
    puts: items.filter((item) => item.right === "PUT").length,
    contracts: contractsOk ? contracts : null,
    exposure: exposureOk ? exposure : null,
    cost: costOk ? cost : null,
    pnl: pnlOk ? pnl : null,
  };
}

function positionExplain(item: BrokerPosition, orders: BrokerOrder[]): Explain {
  const entry = asNumber(item.avg_entry_price);
  const days = dte(item.expiration);
  const order = orders.find((row) => row.symbol === item.symbol && row.position_intent === "buy_to_open");
  const exitOrder = orders.find((row) => row.symbol === item.symbol && row.position_intent === "sell_to_close");
  const lines = [
    `נכס בסיס ${shown(item.underlying)}`,
    `חוזה ${shown(item.symbol)}`,
    `${shown(item.right)} · strike ${shown(item.strike)} · פקיעה ${shown(item.expiration)}`,
    `DTE ${days === null ? "אין נתונים" : days}`,
    `כמות ${shown(item.qty)}`,
    `כניסה ${shown(item.avg_entry_price, "$")}`,
    `מחיר Alpaca ${shown(item.current_price, "$")}`,
    "מחיר ThetaData: אין ציטוט מחובר לפוזיציה",
    `שווי ${shown(item.market_value, "$")}`,
    `עלות ${shown(item.cost_basis, "$")}`,
    `רווח/הפסד ${item.current_price ? shown(item.unrealized_pl, "$") : "אין נתונים"}`,
    `תשואה ${item.current_price && item.unrealized_plpc ? percent(Number(item.unrealized_plpc) * 100) : "אין נתונים"}`,
    `סטופ ראשוני ${entry === null ? "אין נתונים" : money(entry * 0.6)}`,
    `הגנה אחרי +1R ${entry === null ? "אין נתונים" : money(entry * 0.9)}`,
    "Trailing כבוי",
    "זמן בעסקה: אין חותמת כניסה על אובייקט הפוזיציה",
    `פקודת כניסה ${order?.internal_state || "אין נתונים"}`,
    `פקודת יציאה ${exitOrder?.internal_state || "אין נתונים"}`,
  ];
  return {
    title: item.symbol || "פוזיציה",
    does: "חוזה פתוח שאושר אצל Alpaca Paper. הרמות של סטופ ויעד מחושבות מהכניסה. הן אינן פקודה שמורה אצל הברוקר.",
    tools: ["Alpaca positions", "OCC", "מנוע יציאה"],
    formula: `${lines.join("\n")}\nstop = כניסה × 0.60\nאחרי +1R הסטופ = כניסה − 0.25R\n0DTE ≤ 90 דקות\n1DTE ≤ 180 דקות\nחשיפה = |market_value|`,
  };
}

export default function PositionsPage() {
  const [items, setItems] = useState<BrokerPosition[] | null>(null);
  const [orders, setOrders] = useState<BrokerOrder[]>([]);
  const [risk, setRisk] = useState<Risk | null>(null);
  const [failed, setFailed] = useState(false);
  const [open, setOpen] = useState<Explain | null>(null);

  useEffect(() => {
    let alive = true;
    const load = () => {
      Promise.all([
        apiGet<{ items: BrokerPosition[] }>("/api/v1/broker/alpaca/positions"),
        apiGet<{ items: BrokerOrder[] }>("/api/v1/broker/alpaca/orders?status=all"),
        apiGet<Risk>("/api/v1/risk"),
      ])
        .then(([positions, orderBody, riskBody]) => {
          if (!alive) return;
          setItems(positions.items);
          setOrders(orderBody.items);
          setRisk(riskBody);
          setFailed(false);
        })
        .catch(() => {
          if (!alive) return;
          setFailed(true);
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

  const stats = book(items);
  const maxPositions = risk?.max_positions;
  const exposureLimit = asNumber(risk?.exposure_limit ?? undefined);

  const cubes: { label: string; value: string; explain: Explain }[] = [
    {
      label: "פוזיציות",
      value: stats ? (maxPositions == null ? String(stats.count) : `${stats.count} / ${maxPositions}`) : "אין נתונים",
      explain: {
        title: "פוזיציות פתוחות",
        does: "כמה חוזים פתוחים עכשיו אצל Alpaca Paper, מול תקרת הפוזיציות המקבילות.",
        tools: ["Alpaca positions", "trading.toml"],
        formula: "פתוח = len(positions)\nתקרה = max_concurrent_positions = 5\nBUY חדש נחסם כשפתוח ≥ 5",
      },
    },
    {
      label: "CALL",
      value: stats ? String(stats.calls) : "אין נתונים",
      explain: {
        title: "CALL",
        does: "כמה מהפוזיציות הפתוחות הן אופציית רכש.",
        tools: ["סימול OCC"],
        formula: "CALL אם האות בסימול היא C",
      },
    },
    {
      label: "PUT",
      value: stats ? String(stats.puts) : "אין נתונים",
      explain: {
        title: "PUT",
        does: "כמה מהפוזיציות הפתוחות הן אופציית מכר.",
        tools: ["סימול OCC"],
        formula: "PUT אם האות בסימול היא P",
      },
    },
    {
      label: "חוזים",
      value: stats ? (stats.contracts === null ? "אין נתונים" : String(stats.contracts)) : "אין נתונים",
      explain: {
        title: "חוזים",
        does: "סך החוזים הפתוחים. כל חוזה מייצג 100 מניות.",
        tools: ["Alpaca qty"],
        formula: "חוזים = Σ |qty|\nמכפיל = 100",
      },
    },
    {
      label: "עלות",
      value: stats ? (stats.cost === null ? "אין נתונים" : money(stats.cost)) : "אין נתונים",
      explain: {
        title: "עלות",
        does: "עלות הכניסה של הפוזיציות הפתוחות בחשבון Alpaca Paper.",
        tools: ["Alpaca cost_basis"],
        formula: "עלות = Σ |cost_basis|",
      },
    },
    {
      label: "חשיפה",
      value: stats ? (stats.exposure === null ? "אין נתונים" : money(stats.exposure)) : "אין נתונים",
      explain: {
        title: "חשיפה",
        does: "שווי השוק הפתוח. רשימה ריקה מאושרת היא אפס. חוזה בלי שווי שוק משאיר את הסכום בלי נתון.",
        tools: ["Alpaca market_value"],
        formula: "חשיפה = Σ |market_value|\nתקרה = הון × 35%",
      },
    },
    {
      label: "רווח/הפסד",
      value: stats ? (stats.pnl === null ? "אין נתונים" : money(stats.pnl)) : "אין נתונים",
      explain: {
        title: "רווח/הפסד לא ממומש",
        does: "סכום הרווח הפתוח ש-Alpaca מחזיר, רק כשיש מחיר נוכחי. בלי מחיר אין סיכום.",
        tools: ["Alpaca unrealized_pl", "current_price"],
        formula: "לא ממומש = Σ unrealized_pl\nמוצג רק כש-current_price קיים\nתשואה = unrealized_plpc",
      },
    },
    {
      label: "תקרת חשיפה",
      value: exposureLimit === null ? "אין נתונים" : money(exposureLimit),
      explain: {
        title: "תקרת חשיפה",
        does: "המקסימום המותר לפרמיה פתוחה. מעבר לתקרה חוסם BUY חדש.",
        tools: ["הון Alpaca Paper", "trading.toml"],
        formula: "תקרה = equity × 0.35",
      },
    },
  ];
  const rows = [cubes.slice(0, 4), cubes.slice(4)];

  return (
    <Shell>
      <section className="engine">
        <header className="ops-head">
          <div>
            <h1>פוזיציות פתוחות</h1>
            <p>Open Positions · PAPER</p>
          </div>
          <span className={`ops-pip ${failed ? "warn" : items ? "on" : "warn"}`}>
            <i />
            {failed ? "אין נתונים" : items ? "Alpaca Paper" : "טוען"}
          </span>
        </header>

        <div className="engine-board" aria-label="סיכום פוזיציות">
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

        <div className="engine-stages" aria-label="ספר Alpaca">
          {items && items.length === 0 ? (
            <button
              type="button"
              className="engine-stage"
              onClick={() =>
                setOpen({
                  title: "ספר Alpaca Paper",
                  does: "זה הספר הרשמי. רשימה ריקה מאשרת שאין חוזה פתוח בחשבון ה-PAPER.",
                  tools: ["Alpaca positions"],
                  formula: "פתוח = len(positions)\nרשימה ריקה = 0\nמחיר ThetaData נפרד, ובלי ציטוט אין סימון",
                })
              }
            >
              <span>Alpaca Paper</span>
              <b>רשימה ריקה</b>
            </button>
          ) : null}
          {items?.map((item, index) => (
            <button
              key={item.symbol}
              type="button"
              className="engine-stage"
              style={{ animationDelay: `${index * 35}ms` }}
              onClick={() => setOpen(positionExplain(item, orders))}
            >
              <span className="num">{item.underlying || item.symbol} · {item.right || "אין נתונים"} · {shown(item.qty)} חוזים</span>
              <b className="num">{item.current_price && item.unrealized_pl ? money(Number(item.unrealized_pl)) : "אין נתונים"}</b>
            </button>
          ))}
          {failed && !items ? (
            <p className="ops-line">אין נתונים. השרת לא החזיר את ספר Alpaca.</p>
          ) : null}
        </div>
      </section>

      {open ? (
        <div className="engine-backdrop" onClick={() => setOpen(null)}>
          <article className="engine-modal" role="dialog" aria-modal="true" aria-labelledby="position-title" onClick={(event) => event.stopPropagation()}>
            <header>
              <div>
                <p>פוזיציות · PAPER</p>
                <h2 id="position-title">{open.title}</h2>
              </div>
              <button type="button" onClick={() => setOpen(null)}>סגור</button>
            </header>
            <h3>מה הנתון</h3>
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
