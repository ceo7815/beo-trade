"use client";

import { useEffect, useState } from "react";
import { Shell } from "@/components/Shell";
import { TradeStory, type Story } from "@/components/TradeStory";
import { apiGet, type SystemStatus } from "@/lib/api";
import { money } from "@/lib/pnl";

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
};

type Levels = {
  available: boolean;
  stage?: "AT_RISK" | "PROTECTED" | "TRAILING";
  entry?: string | null;
  stop?: string | null;
  protect_at?: string | null;
  protected_stop?: string | null;
  trail_on?: string | null;
  trailing_pct?: number;
  peak?: string | null;
  trail_trigger?: string | null;
  active_exit?: string | null;
  pnl_at_active_exit?: string | null;
  dte?: number | null;
  time_exit_at?: string | null;
  time_exit_passed?: boolean;
  close_exit_minutes?: number | null;
  from_record?: boolean;
};

type OpenTrade = {
  symbol: string;
  opened_at?: string | null;
  levels?: Levels | null;
  story?: Story | null;
};

type Report = {
  open?: OpenTrade[];
  summary?: { realized_pnl?: string; closed_count?: number; wins?: number; losses?: number } | null;
};

type Risk = { max_positions?: number | null; exposure_limit?: string | null };

const STAGES: Record<string, { label: string; hint: string }> = {
  AT_RISK: { label: "בסיכון", hint: "עדיין לא הגיעה לרווח של R אחד. הסטופ המקורי פעיל." },
  PROTECTED: { label: "מוגנת", hint: "הרוויחה R אחד. הסטופ הועלה לקרבת מחיר הכניסה." },
  TRAILING: { label: "סטופ נגרר", hint: "הרוויחה 1.5R. הרווח ננעל ועולה עם השיא." },
};

function num(value: string | number | null | undefined) {
  if (value === null || value === undefined || value === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function px(value: string | number | null | undefined) {
  const parsed = num(value);
  return parsed === null ? "—" : parsed.toFixed(2);
}

function signedMoney(value: number | null) {
  if (value === null) return "אין נתונים";
  return `${value > 0 ? "+" : ""}${money(value)}`;
}

function signedPct(value: number | null) {
  if (value === null) return "—";
  return `${value > 0 ? "+" : ""}${value.toFixed(2)}%`;
}

function tone(value: number | null) {
  if (value === null || value === 0) return "flat";
  return value > 0 ? "up" : "down";
}

function clock(value: string | number | null | undefined) {
  if (value === null || value === undefined || value === "") return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit", timeZone: "Asia/Dubai" }).format(date);
}

function span(ms: number) {
  const minutes = Math.round(Math.abs(ms) / 60000);
  if (minutes < 60) return `${minutes} דק׳`;
  const hours = Math.floor(minutes / 60);
  return `${hours} ש׳ ${minutes % 60} דק׳`;
}

function expiryLabel(dte: number | null | undefined) {
  if (dte === null || dte === undefined) return "פקיעה לא ידועה";
  if (dte <= 0) return "פוקע היום";
  if (dte === 1) return "פוקע מחר";
  return `פוקע בעוד ${dte} ימים`;
}

export default function PositionsPage() {
  const [positions, setPositions] = useState<BrokerPosition[] | null>(null);
  const [report, setReport] = useState<Report | null>(null);
  const [risk, setRisk] = useState<Risk | null>(null);
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [failed, setFailed] = useState(false);
  const [updated, setUpdated] = useState<number | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const [picked, setPicked] = useState<{ position: BrokerPosition; trade: OpenTrade | null } | null>(null);

  useEffect(() => {
    let alive = true;
    const fast = () => {
      apiGet<{ items: BrokerPosition[] }>("/api/v1/broker/alpaca/positions")
        .then((body) => {
          if (!alive) return;
          setPositions(body.items || []);
          setFailed(false);
          setUpdated(Date.now());
        })
        .catch(() => alive && setFailed(true));
    };
    const slow = () => {
      Promise.allSettled([
        apiGet<Report>("/api/v1/report/trades?period=today"),
        apiGet<Risk>("/api/v1/risk"),
        apiGet<SystemStatus>("/api/v1/system"),
      ]).then(([reportBody, riskBody, statusBody]) => {
        if (!alive) return;
        if (reportBody.status === "fulfilled") setReport(reportBody.value);
        if (riskBody.status === "fulfilled") setRisk(riskBody.value);
        if (statusBody.status === "fulfilled") setStatus(statusBody.value);
      });
    };
    fast();
    slow();
    const fastTimer = window.setInterval(fast, 8000);
    const slowTimer = window.setInterval(slow, 20000);
    const tick = window.setInterval(() => setNow(Date.now()), 1000);
    return () => {
      alive = false;
      window.clearInterval(fastTimer);
      window.clearInterval(slowTimer);
      window.clearInterval(tick);
    };
  }, []);

  useEffect(() => {
    if (!picked) return;
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && setPicked(null);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [picked]);

  const tradeOf = (symbol: string | undefined) => {
    const key = (symbol || "").replace(/\s/g, "");
    return report?.open?.find((item) => item.symbol.replace(/\s/g, "") === key) ?? null;
  };

  const items = positions ?? [];
  const cost = items.reduce((sum, item) => sum + Math.abs(num(item.cost_basis) ?? 0), 0);
  const open = items.every((item) => num(item.unrealized_pl) !== null) ? items.reduce((sum, item) => sum + (num(item.unrealized_pl) ?? 0), 0) : null;
  const exposure = items.reduce((sum, item) => sum + Math.abs(num(item.market_value) ?? 0), 0);
  const limit = num(risk?.exposure_limit);
  const atStops = items.map((item) => num(tradeOf(item.symbol)?.levels?.pnl_at_active_exit)).filter((value): value is number => value !== null);
  const worstCase = atStops.length === items.length && items.length > 0 ? atStops.reduce((sum, value) => sum + value, 0) : null;
  const realized = num(report?.summary?.realized_pnl);
  const sessionClose = status?.session_close ? new Date(status.session_close).getTime() : null;
  const age = updated === null ? null : Math.max(0, Math.round((now - updated) / 1000));

  return (
    <Shell>
      <section className="live">
        <header className="ops-head">
          <div>
            <h1>מרכז בקרה · פוזיציות פתוחות</h1>
            <p>כל פוזיציה: איפה היא עכשיו, מה יוציא אותה, ומתי</p>
          </div>
          <div className="ops-pips">
            <span className={`ops-pip ${failed ? "warn" : positions ? "on" : "warn"}`}>
              <i />
              {failed ? "אין חיבור לברוקר" : age === null ? "טוען" : `חי · עודכן לפני ${age} שנ׳`}
            </span>
            <span className="ops-pip">PAPER</span>
            {sessionClose ? <span className="ops-pip num">סגירת מסחר {clock(sessionClose)} · עוד {span(sessionClose - now)}</span> : null}
          </div>
        </header>

        <div className="live-strip">
          <Stat label="פתוחות" value={positions === null ? "—" : `${items.length}${risk?.max_positions ? ` / ${risk.max_positions}` : ""}`} />
          <Stat label="כסף בסיכון" value={positions === null ? "—" : money(cost)} />
          <Stat label="רווח/הפסד פתוח" value={signedMoney(open)} sub={open !== null && cost > 0 ? signedPct((open / cost) * 100) : undefined} className={tone(open)} />
          <Stat label="אם הכול ייצא בסטופ הנוכחי" value={signedMoney(worstCase)} className={tone(worstCase)} />
          <Stat label="ממומש היום" value={signedMoney(realized)} sub={report?.summary ? `${report.summary.wins ?? 0} ברווח · ${report.summary.losses ?? 0} בהפסד` : undefined} className={tone(realized)} />
          <div className="live-stat">
            <span>חשיפה מול תקרה</span>
            <b className="num">{limit ? `${money(exposure)} / ${money(limit)}` : money(exposure)}</b>
            <div className="ops-track"><i style={{ width: `${limit ? Math.min(100, (exposure / limit) * 100) : 0}%` }} /></div>
          </div>
        </div>

        {positions !== null && items.length === 0 ? (
          <div className="live-empty">אין פוזיציה פתוחה ב-Alpaca Paper. המערכת ממשיכה לסרוק.</div>
        ) : null}
        {failed && positions === null ? <p className="ops-line">השרת לא החזיר את ספר Alpaca.</p> : null}

        <div className="live-grid">
          {items.map((item) => (
            <Card
              key={item.symbol}
              position={item}
              trade={tradeOf(item.symbol)}
              now={now}
              sessionClose={sessionClose}
              onOpen={() => setPicked({ position: item, trade: tradeOf(item.symbol) })}
            />
          ))}
        </div>
      </section>

      {picked ? (
        <div className="engine-backdrop" onClick={() => setPicked(null)}>
          <article className="engine-modal" role="dialog" aria-modal="true" aria-labelledby="live-title" onClick={(event) => event.stopPropagation()}>
            <header>
              <div>
                <p>פוזיציה פתוחה · PAPER · נפתחה {clock(picked.trade?.opened_at)}</p>
                <h2 id="live-title">{picked.position.underlying} {picked.position.right} {picked.position.strike}</h2>
              </div>
              <button type="button" onClick={() => setPicked(null)}>סגור</button>
            </header>
            <TradeStory story={picked.trade?.story} />
          </article>
        </div>
      ) : null}
    </Shell>
  );
}

function Stat({ label, value, sub, className = "" }: { label: string; value: string; sub?: string; className?: string }) {
  return (
    <div className="live-stat">
      <span>{label}</span>
      <b className={`num ${className}`}>{value}</b>
      {sub ? <em className={className}>{sub}</em> : null}
    </div>
  );
}

function Card({ position, trade, now, sessionClose, onOpen }: { position: BrokerPosition; trade: OpenTrade | null; now: number; sessionClose: number | null; onOpen: () => void }) {
  const levels = trade?.levels?.available ? trade.levels : null;
  const pnl = num(position.unrealized_pl);
  const pct = num(position.unrealized_plpc);
  const current = num(position.current_price);
  const active = num(levels?.active_exit);
  const stage = levels?.stage ?? "AT_RISK";
  const opened = trade?.opened_at ? new Date(trade.opened_at).getTime() : null;
  const timeExit = levels?.time_exit_at ? new Date(levels.time_exit_at).getTime() : null;
  const closeExit = sessionClose !== null && levels?.close_exit_minutes != null ? sessionClose - levels.close_exit_minutes * 60000 : null;
  const room = current !== null && active !== null && current > 0 ? ((current - active) / current) * 100 : null;
  const atExit = num(levels?.pnl_at_active_exit);
  return (
    <article className={`live-card ${stage.toLowerCase()}`}>
      <header>
        <div>
          <h2 className="num">{position.underlying} {position.right} {position.strike}</h2>
          <p>
            {position.qty} חוזים · {expiryLabel(levels?.dte)}
            {opened ? ` · נפתחה ${clock(opened)} (לפני ${span(now - opened)})` : ""}
          </p>
        </div>
        <span className="live-stage" title={STAGES[stage]?.hint}>{STAGES[stage]?.label ?? stage}</span>
      </header>

      <div className="live-pnl">
        <b className={`num ${tone(pnl)}`}>{signedMoney(pnl)}</b>
        <span className={`num ${tone(pct)}`}>{pct === null ? "—" : signedPct(pct * 100)}</span>
        <em>הושקע {money(num(position.cost_basis) ?? 0)}</em>
      </div>

      {levels ? <Ladder levels={levels} current={current} /> : <p className="live-note">אין עדיין רמות יציאה לחוזה הזה.</p>}

      <dl className="live-facts">
        <div><dt>כניסה</dt><dd className="num">{px(position.avg_entry_price)}</dd></div>
        <div><dt>עכשיו</dt><dd className="num">{px(position.current_price)}</dd></div>
        <div><dt>שיא</dt><dd className="num">{px(levels?.peak)}</dd></div>
        <div>
          <dt>יציאה תופעל ב-</dt>
          <dd className="num">{px(active)}{room !== null ? <small> · עוד {room.toFixed(1)}%</small> : null}</dd>
        </div>
        <div><dt>אם תופעל עכשיו</dt><dd className={`num ${tone(atExit)}`}>{signedMoney(atExit)}</dd></div>
        <div>
          <dt>יציאת זמן</dt>
          <dd className="num">
            {timeExit ? (levels?.time_exit_passed || timeExit <= now ? "הגיע הזמן · ממתין למחיר" : `${clock(timeExit)} · עוד ${span(timeExit - now)}`) : "אין מגבלת זמן"}
          </dd>
        </div>
        {closeExit ? (
          <div><dt>יציאה לפני הסגירה</dt><dd className="num">{clock(closeExit)} · עוד {span(closeExit - now)}</dd></div>
        ) : null}
      </dl>

      <footer>
        <button type="button" onClick={onOpen}>למה נכנסנו ומה יוציא אותה</button>
        {levels && !levels.from_record ? <span className="live-note">רמות מחושבות מהכניסה. אין רשומת מעקב שמורה.</span> : null}
      </footer>
    </article>
  );
}

function Ladder({ levels, current }: { levels: Levels; current: number | null }) {
  const marks = [
    { key: "stop", label: "סטופ", value: num(levels.stop) },
    { key: "entry", label: "כניסה", value: num(levels.entry) },
    { key: "protect", label: "הגנה", value: num(levels.protect_at) },
    { key: "trail", label: "נגרר", value: num(levels.trail_on) },
  ].filter((mark): mark is { key: string; label: string; value: number } => mark.value !== null);
  const peak = num(levels.peak);
  const exit = num(levels.active_exit);
  const values = [...marks.map((mark) => mark.value), peak, exit, current].filter((value): value is number => value !== null);
  if (values.length < 2) return null;
  const low = Math.min(...values) * 0.97;
  const high = Math.max(...values) * 1.03;
  const at = (value: number) => `${((value - low) / (high - low)) * 100}%`;
  const entry = num(levels.entry);
  return (
    <div className="live-ladder" aria-label="סולם מחיר">
      <div className="live-rail">
        {entry !== null && current !== null ? (
          <i className={`live-fill ${current >= entry ? "up" : "down"}`} style={{ left: at(Math.min(entry, current)), width: `calc(${at(Math.max(entry, current))} - ${at(Math.min(entry, current))})` }} />
        ) : null}
        {marks.map((mark) => (
          <span key={mark.key} className={`live-mark ${mark.key}`} style={{ left: at(mark.value) }}>
            <em>{mark.label}</em>
            <small className="num">{mark.value.toFixed(2)}</small>
          </span>
        ))}
        {exit !== null ? <span className="live-exit" style={{ left: at(exit) }} title={`יציאה ב-${exit.toFixed(2)}`} /> : null}
        {peak !== null && entry !== null && peak > entry ? <span className="live-peak" style={{ left: at(peak) }} title={`שיא ${peak.toFixed(2)}`} /> : null}
        {current !== null ? <span className="live-now" style={{ left: at(current) }}><b className="num">{current.toFixed(2)}</b></span> : null}
      </div>
    </div>
  );
}
