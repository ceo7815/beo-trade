"use client";

import { useEffect, useState } from "react";
import { FloorMark } from "@/components/FloorMark";
import { ProtectionBar, type Protection } from "@/components/ProtectionBar";
import { useLive } from "@/lib/live";
import { exitLabel } from "@/lib/exits";
import { Shell } from "@/components/Shell";
import { copy } from "@/lib/copy";
import { apiGet, poll, transportMode } from "@/lib/api";

type Risk = {
  state: string;
  per_trade_limit: string | null;
  exposure_limit: string | null;
  daily_loss_limit: string | null;
  daily_loss_used: string | null;
  daily_loss_state?: string | null;
  day_floor?: { armed: boolean; closed: boolean; arm: string; floor: string } | null;
  day_pnl_note?: string | null;
  protection?: Protection | null;
  max_positions: number;
  open_positions: number | null;
  max_spread: string;
  max_slippage: string;
  sector_note: string;
};

type Policy = {
  profile?: string;
  risk_per_trade_pct?: number;
  normal_position_capital_pct?: number;
  hard_position_capital_pct?: number;
  max_open_positions?: number;
  max_total_premium_exposure_pct?: number;
  max_aggregate_planned_risk_pct?: number;
  max_sector_exposure_pct?: number;
  max_positions_per_underlying?: number;
  daily_warning_pct?: number;
  daily_entry_stop_pct?: number;
  daily_hard_stop_pct?: number;
  daily_kill_loss_usd?: number;
  initial_stop_decline_pct?: number;
  protect_at_r?: number;
  protective_stop_r?: number;
  trail_activate_r?: number;
  winner_run_r?: number;
  trailing_pct?: number;
  holding_minutes_0dte?: number;
  holding_minutes_1dte?: number;
  example_equity?: string;
};

type Trace = {
  risk_budget?: string;
  contract_cost?: string;
  risk_per_contract?: string;
  by_risk?: number;
  by_capital?: number;
  by_exposure?: number;
  by_sector?: number;
  by_aggregate?: number;
  by_buying_power?: number | null;
  quantity?: number;
  limiter?: string;
};

type ClosedTrade = {
  symbol?: string;
  exit_reason?: string;
  entry_price?: string;
  exit_price?: string;
  pnl?: string;
  return_pct?: string;
  opened_at?: string | null;
  closed_at?: string | null;
};

type Explain = { title: string; does: string; tools: string[]; formula: string };

function pct(value: number | undefined) {
  if (value == null || !Number.isFinite(value)) return "אין נתונים";
  const shown = (value * 100).toFixed(1).replace(/\.0$/, "");
  return `${shown}%`;
}

function money(value: string | null | undefined) {
  if (value == null || value === "") return "אין נתונים";
  const number = Number(value);
  if (!Number.isFinite(number)) return "אין נתונים";
  return number.toLocaleString("en-US", { style: "currency", currency: "USD" });
}

function ratio(value: string | null | undefined) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "אין נתונים";
  return number <= 1 ? `${(number * 100).toFixed(0)}%` : String(value);
}

function near(used: string | null | undefined, limit: string | null | undefined) {
  const left = Number(used);
  const right = Number(limit);
  if (!Number.isFinite(left) || !Number.isFinite(right) || right <= 0) return false;
  return left / right >= 0.8;
}

function pair(used: string, limit: string) {
  if (used === "אין נתונים" && limit === "אין נתונים") return "אין נתונים";
  return `${used} / ${limit}`;
}

export default function RiskPage() {
  const live = useLive();
  const [body, setBody] = useState<Risk | null>(null);
  const [policy, setPolicy] = useState<Policy | null>(null);
  const [trace, setTrace] = useState<Trace | null>(null);
  const [closed, setClosed] = useState<ClosedTrade[] | null>(null);
  const [exposure, setExposure] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  const [open, setOpen] = useState<Explain | null>(null);

  useEffect(() => {
    let alive = true;
    const load = () => {
      return Promise.allSettled([
        apiGet<Risk>("/api/v1/risk"),
        apiGet<{ available?: boolean; exposure?: string | null; closed_trades?: ClosedTrade[] }>("/api/v1/finance"),
        apiGet<Policy>("/api/v1/policy"),
        apiGet<{ items: { policy?: Trace }[] }>("/api/v1/decisions?limit=20"),
      ]).then(([riskResult, financeResult, policyResult, decisionResult]) => {
        if (!alive) return;
        if (riskResult.status === "fulfilled") {
          setBody(riskResult.value);
          setFailed(false);
        } else {
          setFailed(true);
        }
        if (financeResult.status === "fulfilled" && financeResult.value.available !== false) {
          setExposure(financeResult.value.exposure ?? null);
          setClosed(financeResult.value.closed_trades ?? []);
        }
        setPolicy(policyResult.status === "fulfilled" ? policyResult.value : null);
        const items = decisionResult.status === "fulfilled" ? decisionResult.value.items : [];
        const latest = items.find((item) => item.policy && Object.keys(item.policy).length > 0);
        setTrace(latest?.policy ?? null);
      });
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

  const positions = body?.open_positions == null ? "אין נתונים" : body.max_positions == null ? String(body.open_positions) : `${body.open_positions} / ${body.max_positions}`;
  const cubes: { label: string; value: string; explain: Explain }[] = [
    {
      label: "סטטוס",
      value: body?.state || (failed ? "אין נתונים" : "טוען"),
      explain: {
        title: "סטטוס סיכון",
        does: "בתוך המגבלה כל עוד ההפסד היומי והפוזיציות לא חצו את התקרה. אזהרה כשהפוזיציות מלאות. עצור כשההפסד היומי הגיע לתקרה.",
        tools: ["Alpaca equity", "trading.toml"],
        formula: "אזהרה: −P&L ≥ הון × 2.5%\nעצירת כניסות: −P&L ≥ הון × 4%\nעצירה יומית: −P&L ≥ הון × 5%",
      },
    },
    {
      label: "לעסקה",
      value: money(body?.per_trade_limit),
      explain: {
        title: "מגבלת הון לעסקה",
        does: "הסכום המרבי לעסקת BUY אחת. אותו אחוז שקובע את הכמות במנוע.",
        tools: ["הון Alpaca Paper"],
        formula: "הון רגיל = הון × 7%\nתקרה קשיחה = הון × 10%",
      },
    },
    {
      label: "חשיפה",
      value: pair(money(exposure), money(body?.exposure_limit)),
      explain: {
        title: "מגבלת חשיפה",
        does: "השווי הפתוח מול התקרה. אותו שווי שמופיע בפוזיציות ובבקרת הכספים.",
        tools: ["Alpaca market_value"],
        formula: "פתוח = Σ |market_value|\nתקרה = הון × 35%",
      },
    },
    {
      label: "הפסד יומי",
      value: pair(money(body?.daily_loss_used), money(body?.daily_loss_limit)),
      explain: {
        title: "מגבלת הפסד יומי",
        does: "כמה מההפסד היומי כבר נוצל. BUY חדש נעצר כשההפסד מגיע לתקרה.",
        tools: ["equity", "last_equity"],
        formula: "היום = שינוי הון − הפקדות ומשיכות של אותו יום\nאזהרה 2.5% · עצירת כניסות 4% · עצירה 5%",
      },
    },
    {
      label: "פוזיציות",
      value: positions,
      explain: {
        title: "פוזיציות פתוחות",
        does: "כמה חוזים פתוחים מול התקרה. אותו יחס שמופיע במסך הפוזיציות.",
        tools: ["Alpaca positions"],
        formula: "פתוח = len(positions)\nתקרה = 10\nנכס אחד = פוזיציה אחת",
      },
    },
    {
      label: "מרווח",
      value: ratio(body?.max_spread),
      explain: {
        title: "מרווח מקסימלי",
        does: "חוזה נפסל אם המרווח בין ה-bid ל-ask גדול מהסף.",
        tools: ["ThetaData quote"],
        formula: "spread = (ask − bid) / mid\nסף = 0.10",
      },
    },
    {
      label: "סטייה",
      value: ratio(body?.max_slippage),
      explain: {
        title: "סטיית מחיר",
        does: "המרווח המותר מעל מחיר ה-ask בבדיקת הכניסה.",
        tools: ["trading.toml"],
        formula: "תקרה = ask × (1 + 0.01)",
      },
    },
    {
      label: "סקטור",
      value: "לא זמין",
      explain: {
        title: "חשיפת סקטור",
        does: "אין מקור סקטור אמיתי. בלי שיוך אין מספר, והמנוע עדיין סופר דלי לא מסווג.",
        tools: ["trading.toml"],
        formula: "תקרה = הון × 10%\nדלי בלי סקטור = UNCLASSIFIED",
      },
    },
  ];
  const liveFloor = transportMode() === "sse" ? live.snap?.day?.day_floor : undefined;
  const floor = liveFloor ?? body?.day_floor ?? live.snap?.day?.day_floor ?? null;
  const protection = (transportMode() === "sse" ? live.snap?.day?.protection : undefined) ?? body?.protection ?? live.snap?.day?.protection ?? null;
  const dayState = transportMode() === "sse" ? live.snap?.day?.daily_loss_state : body?.daily_loss_state;
  const dayNote = (transportMode() === "sse" ? live.snap?.day?.day_pnl_note : null) ?? body?.day_pnl_note ?? live.snap?.day?.day_pnl_note ?? null;
  const unknown = !dayNote && (dayState === "unknown" || (dayState !== "hard_stop" && protection?.status === "unknown"));
  const tone = unknown ? "warn" : body?.state === "עצור" ? "off" : body?.state === "אזהרה" || failed ? "warn" : body ? "on" : "warn";
  const badge = unknown ? "לא ידוע" : (body?.state || (failed ? "אין נתונים" : "טוען"));

  return (
    <Shell>
      <section className="engine">
        <header className="ops-head">
          <div>
            <h1>{copy.screens.risk}</h1>
            <p>Risk Control · PAPER</p>
          </div>
          <span className={`ops-pip ${tone}`}>
            <i />
            {badge}
          </span>
        </header>

        <div className="plain-list">
          <div className="floor-host">
            <button type="button" className="plain-row" onClick={() => setOpen(cubes[3].explain)}>
              <b>הפסד היום</b>
              <strong className={`num ${near(body?.daily_loss_used, body?.daily_loss_limit) ? "bad" : ""}`}>{pair(money(body?.daily_loss_used), money(body?.daily_loss_limit))}</strong>
            </button>
            {floor?.armed && floor.floor ? <FloorMark closed={Boolean(floor.closed)} amount={String(floor.floor)} /> : unknown ? <span className="floor-mark unknown" aria-label="לא ידוע"><b>לא ידוע</b></span> : null}
          </div>
          {protection || unknown ? <ProtectionBar protection={unknown ? { status: "unknown", line: "לא ידוע" } : protection} /> : null}
          <button type="button" className="plain-row" onClick={() => setOpen(cubes[4].explain)}>
            <b>פוזיציות</b>
            <strong className={`num ${near(body?.open_positions == null ? null : String(body.open_positions), body?.max_positions == null ? null : String(body.max_positions)) ? "bad" : ""}`}>{positions}</strong>
          </button>
          <button type="button" className="plain-row" onClick={() => setOpen(cubes[2].explain)}>
            <b>חשיפה</b>
            <strong className={`num ${near(exposure, body?.exposure_limit) ? "bad" : ""}`}>{pair(money(exposure), money(body?.exposure_limit))}</strong>
          </button>
        </div>

        <details className="desk-system">
        <summary>מדיניות</summary>
        <div className="policy-sheet">
          <article>
            <h2>מדיניות הסיכון</h2>
            <dl>
              <dt>רמת סיכון</dt><dd>{policy?.profile === "AGGRESSIVE" ? "אגרסיבית" : "אין נתונים"}</dd>
              <dt>סיכון לעסקה</dt><dd className="num">{pct(policy?.risk_per_trade_pct)}</dd>
              <dt>הון רגיל לעסקה</dt><dd className="num">{pct(policy?.normal_position_capital_pct)}</dd>
              <dt>תקרת עסקה</dt><dd className="num">{pct(policy?.hard_position_capital_pct)}</dd>
              <dt>מקסימום פוזיציות</dt><dd className="num">{policy?.max_open_positions ?? "אין נתונים"}</dd>
              <dt>חשיפה כוללת</dt><dd className="num">{pct(policy?.max_total_premium_exposure_pct)}</dd>
              <dt>סיכון מצטבר</dt><dd className="num">{pct(policy?.max_aggregate_planned_risk_pct)}</dd>
              <dt>חשיפת סקטור</dt><dd className="num">{pct(policy?.max_sector_exposure_pct)}</dd>
              <dt>נכס אחד</dt><dd>{policy?.max_positions_per_underlying === 1 ? "פוזיציה אחת" : "אין נתונים"}</dd>
              <dt>אזהרת הפסד</dt><dd className="num">{policy ? `${pct(policy.daily_warning_pct)}-` : "אין נתונים"}</dd>
              <dt>עצירת כניסות</dt><dd className="num">{policy ? `${pct(policy.daily_entry_stop_pct)}-` : "אין נתונים"}</dd>
              <dt>עצירה יומית</dt><dd className="num">{policy ? `${pct(policy.daily_hard_stop_pct)}-` : "אין נתונים"}</dd>
              <dt>עצירה בדולרים</dt><dd className="num">{policy?.daily_kill_loss_usd != null ? `−$${Number(policy.daily_kill_loss_usd).toLocaleString("en-US")}` : "אין נתונים"}</dd>
            </dl>
          </article>
          <article>
            <h2>מדיניות יציאה</h2>
            <dl>
              <dt>Stop ראשוני</dt><dd>−1R · ברירת מחדל {pct(policy?.initial_stop_decline_pct)} מהפרמיה</dd>
              <dt>ב-+1R</dt><dd>{policy ? "מצב הגנה" : "אין נתונים"}</dd>
              <dt>{copy.riskLabels.protected}</dt><dd className="num">{policy ? `−${policy.protective_stop_r}R` : "אין נתונים"}</dd>
              <dt>ב־30%</dt><dd>{policy ? copy.riskLabels.trailOn : "אין נתונים"}</dd>
              <dt>ב-+2R</dt><dd>{policy ? "Winner Run" : "אין נתונים"}</dd>
              <dt>{copy.riskLabels.trail}</dt><dd className="num">{policy ? `${pct(policy.trailing_pct)} מהשיא` : "אין נתונים"}</dd>
              <dt>0DTE</dt><dd className="num">{policy ? `עד ${policy.holding_minutes_0dte} דקות` : "אין נתונים"}</dd>
              <dt>1DTE</dt><dd className="num">{policy ? `עד ${policy.holding_minutes_1dte} דקות` : "אין נתונים"}</dd>
              <dt>סגירת סשן</dt><dd>לפני סיום המסחר</dd>
              <dt>לילה</dt><dd>ללא החזקה ללילה</dd>
            </dl>
          </article>
          <article>
            <h2>איך Beo-Trade מחשבת את גודל העסקה?</h2>
            <pre>{policy ? `הון עצמי × ${pct(policy.risk_per_trade_pct)} = תקציב סיכון\n$${Number(policy.example_equity).toLocaleString("en-US")} × ${pct(policy.risk_per_trade_pct)} = $${(Number(policy.example_equity) * Number(policy.risk_per_trade_pct)).toLocaleString("en-US")}\n\nמחיר אופציה × 100 = עלות חוזה\nעלות חוזה × ${pct(policy.initial_stop_decline_pct)} = סיכון לחוזה\nתקציב סיכון ÷ סיכון לחוזה = כמות לפי סיכון\n\nMIN(Risk, Capital, Exposure, Sector, Aggregate, Buying Power, Max Contracts) = כמות סופית` : "אין נתונים"}</pre>
          </article>
          <article>
            <h2>עקבות עסקה</h2>
            {trace ? (
              <dl>
                <dt>תקציב סיכון</dt><dd className="num">{trace.risk_budget ?? "אין נתונים"}</dd>
                <dt>סיכון לחוזה</dt><dd className="num">{trace.risk_per_contract ?? "אין נתונים"}</dd>
                <dt>כמות לפי סיכון</dt><dd className="num">{trace.by_risk ?? "אין נתונים"}</dd>
                <dt>כמות לפי הון</dt><dd className="num">{trace.by_capital ?? "אין נתונים"}</dd>
                <dt>כמות לפי חשיפה</dt><dd className="num">{trace.by_exposure ?? "אין נתונים"}</dd>
                <dt>כמות לפי סקטור</dt><dd className="num">{trace.by_sector ?? "אין נתונים"}</dd>
                <dt>כמות לפי {copy.riskLabels.buyingPower}</dt><dd className="num">{trace.by_buying_power ?? "אין נתונים"}</dd>
                <dt>כמות סופית</dt><dd className="num">{trace.quantity ?? "אין נתונים"}</dd>
                <dt>הסיבה שהגבילה</dt><dd>{trace.limiter || "אין נתונים"}</dd>
              </dl>
            ) : <p>אין עדיין עסקה</p>}
          </article>
          <article>
            <h2>עקבות יציאה</h2>
            {closed && closed.length > 0 ? closed.slice(0, 8).map((row) => (
              <dl key={`${row.symbol}-${row.closed_at}`}>
                <dt>סיבת יציאה</dt><dd>{row.exit_reason ? exitLabel(row.exit_reason) : "אין נתונים"}</dd>
                <dt>{copy.riskLabels.entry}</dt><dd className="num">{row.entry_price || "אין נתונים"}</dd>
                <dt>{copy.riskLabels.exit}</dt><dd className="num">{row.exit_price || "אין נתונים"}</dd>
                <dt>{copy.riskLabels.pnl}</dt><dd className="num">{row.pnl || "אין נתונים"}</dd>
                <dt>{copy.riskLabels.pnl} %</dt><dd className="num">{row.return_pct || "אין נתונים"}</dd>
              </dl>
            )) : <p>אין עדיין עסקה</p>}
          </article>
        </div>
        </details>
      </section>

      {open ? (
        <div className="engine-backdrop" onClick={() => setOpen(null)}>
          <article className="engine-modal" role="dialog" aria-modal="true" aria-labelledby="risk-title" onClick={(event) => event.stopPropagation()}>
            <header>
              <div>
                <p>בקרת סיכון · PAPER</p>
                <h2 id="risk-title">{open.title}</h2>
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
