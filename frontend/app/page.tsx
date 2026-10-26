"use client";

import { useEffect, useState } from "react";
import { Shell } from "@/components/Shell";
import { PHASES, apiGet, poll, type SystemStatus } from "@/lib/api";
import { TradeStory, type Story } from "@/components/TradeStory";
import { exitLabel } from "@/lib/exits";
import { FloorMark } from "@/components/FloorMark";

type Desk = {
  autonomous: boolean;
  heartbeat_age_seconds: number | null;
  last_scan: {
    observed_at?: string;
    finished_at?: string;
    recommendations?: number;
    buys?: number;
    ai_calls?: number;
    blocked?: string;
    universe?: { discovered?: number; filtered?: number } | null;
    stages?: {
      orders?: number;
      buy_outcomes?: { symbol: string; result: string; reason: string }[];
      ai_failures?: Record<string, number>;
    } | null;
  } | null;
  scan_progress?: { started_at?: string; stage_done?: string; updated_at?: string } | null;
};

type FlowTrade = {
  symbol: string;
  underlying?: string | null;
  right?: string | null;
  qty?: number | string | null;
  qty_open?: number | string | null;
  entry_price?: string | null;
  exit_price?: string | null;
  current_price?: string | null;
  invested?: string | null;
  pnl?: string | null;
  return_pct?: string | null;
  unrealized_pnl?: string | null;
  opened_at?: string | null;
  closed_at?: string | null;
  exit_reason?: string | null;
  held_minutes?: number | null;
  story?: Story | null;
  plan?: { planned_risk?: string | null } | null;
  levels?: { pnl_at_active_exit?: string | null } | null;
};

type DaySummary = {
  closed_count?: number;
  wins?: number;
  losses?: number;
  realized_pnl?: string;
  unrealized_pnl?: string;
  invested_closed?: string;
  open_invested?: string;
  open_count?: number;
};

type TodayReport = {
  summary?: DaySummary | null;
  closed?: FlowTrade[];
  open?: FlowTrade[];
};

type FlowEvent = { at: string; kind: "entry" | "exit" | "blocked"; trade: FlowTrade | null; title: string; lines: string[] };

type BrokerAccount = {
  equity?: string | null;
  cash?: string | null;
  buying_power?: string | null;
  portfolio_value?: string | null;
  last_equity?: string | null;
};

type Position = {
  symbol: string;
  underlying?: string | null;
  right?: string | null;
  qty?: string | null;
  avg_entry_price?: string | null;
  unrealized_pl?: string | null;
  unrealized_plpc?: string | null;
  current_price?: string | null;
  market_value?: string | null;
  cost_basis?: string | null;
};

type ClosedLite = { closed_at?: string | null; pnl?: string | null; invested?: string | null };

type TradeTotals = {
  total_pnl: string;
  total_return_pct: string | null;
  realized_pnl: string;
  ai_cost: string | null;
  net_after_ai: string | null;
};

type Risk = {
  state: string;
  per_trade_limit: string | null;
  exposure_limit: string | null;
  daily_loss_limit: string | null;
  daily_loss_used: string | null;
  today_pnl?: string | null;
  day_floor?: { armed: boolean; closed: boolean; arm: string; floor: string } | null;
  max_positions: number;
  open_positions: number | null;
};

type AuditRow = { state: string; reason: string; created_at: string };
type Order = { symbol?: string; internal_state?: string; qty?: string; position_intent?: string };

function money(value: string | number | null | undefined) {
  if (value === null || value === undefined || value === "") return "אין נתונים";
  const number = Number(value);
  if (!Number.isFinite(number)) return "אין נתונים";
  return number.toLocaleString("en-US", { style: "currency", currency: "USD" });
}

function ageLabel(seconds: number | null | undefined) {
  if (seconds === null || seconds === undefined) return "אין פעימה";
  if (seconds < 60) return `${Math.floor(seconds)} שנ׳`;
  return `${Math.floor(seconds / 60)} דק׳`;
}

function share(value: string | number | null | undefined) {
  if (value === null || value === undefined || value === "") return "—";
  const number = Number(value);
  if (!Number.isFinite(number)) return "—";
  return `${number > 0 ? "+" : ""}${number.toFixed(2)}%`;
}

function tone(value: number | null) {
  if (value === null || value === 0) return "flat";
  return value > 0 ? "up" : "down";
}

function clock(iso: string | null | undefined) {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso.slice(11, 19) || iso;
  return new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit", second: "2-digit", timeZone: "Asia/Dubai" }).format(date);
}

export default function HomePage() {
  return (
    <Shell>
      <Board />
    </Shell>
  );
}

function Board() {
  const [desk, setDesk] = useState<Desk | null>(null);
  const [account, setAccount] = useState<BrokerAccount | null>(null);
  const [positions, setPositions] = useState<Position[] | null>(null);
  const [orders, setOrders] = useState<Order[] | null>(null);
  const [today, setToday] = useState<TodayReport | null>(null);
  const [picked, setPicked] = useState<FlowTrade | null>(null);
  const [audit, setAudit] = useState<AuditRow[] | null>(null);
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [risk, setRisk] = useState<Risk | null>(null);
  const [totals, setTotals] = useState<TradeTotals | null>(null);
  const [book, setBook] = useState<ClosedLite[] | null>(null);
  const [syncedAt, setSyncedAt] = useState<number | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const [beatAt, setBeatAt] = useState<number | null>(null);

  useEffect(() => {
    let alive = true;
    const feed = <T,>(path: string, apply: (body: T) => void, everyMs: number) =>
      poll(async () => {
        const body = await apiGet<T>(path);
        if (!alive) return;
        apply(body);
        setSyncedAt(Date.now());
      }, everyMs);
    const stops = [
      feed<Desk>("/api/v1/desk", setDesk, 5000),
      feed<{ items?: Position[] }>("/api/v1/broker/alpaca/positions", (body) => setPositions(body.items || []), 5000),
      feed<Risk>("/api/v1/risk", setRisk, 8000),
      feed<TodayReport>("/api/v1/report/trades?period=today", setToday, 10000),
      feed<{ items?: Order[] }>("/api/v1/broker/alpaca/orders?status=open", (body) => setOrders(body.items || []), 10000),
      feed<SystemStatus>("/api/v1/system", setStatus, 10000),
      feed<{ items: AuditRow[] }>("/api/v1/audit", (body) => setAudit(body.items || []), 15000),
      feed<{ account?: BrokerAccount }>("/api/v1/broker/alpaca/account", (body) => setAccount(body.account || null), 15000),
      feed<{ summary?: TradeTotals; closed?: ClosedLite[] }>("/api/v1/report/trades?period=all", (body) => {
        setTotals(body.summary ?? null);
        setBook(body.closed ?? []);
      }, 60000),
    ];
    const clockTimer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => {
      alive = false;
      stops.forEach((stop) => stop());
      window.clearInterval(clockTimer);
    };
  }, []);

  useEffect(() => {
    if (desk?.heartbeat_age_seconds != null) setBeatAt(Date.now());
  }, [desk?.heartbeat_age_seconds]);

  const dayPnl = num(risk?.today_pnl);
  const scan = desk?.last_scan ?? null;
  const exposure = exposureOf(positions);
  const stake = stakeOf(today?.open, positions);
  const phase = status ? PHASES[status.market_phase] || status.market_phase : "מתחבר";
  const closed = status?.market_phase === "CLOSED" || status?.market_phase === "POST_MARKET" || status?.market_phase === "DAILY_REPORT";
  const narrative = narrativeOf(desk, scan, closed, phase);
  const sync = syncOf(syncedAt, now);
  const days = recentDays(book);
  const lastDay = days?.[0] ?? null;
  const showLast = closed && (dayPnl === null || dayPnl === 0) && lastDay !== null;

  return (
    <section className="desk-home">
      <header className="desk-hero">
        <div className="desk-hero-top">
          <span>{phase}</span>
          <span className={sync.tone === "on" ? "up" : sync.tone === "off" ? "down" : ""}>{sync.text}</span>
        </div>
        <p className="desk-hero-label">{showLast && lastDay ? dayTitle(lastDay.day) : "היום"}</p>
        <div className="desk-hero-main">
          <p className={`desk-hero-pnl num ${tone(showLast && lastDay ? lastDay.pnl : dayPnl)}`}>{showLast && lastDay ? signedMoney(lastDay.pnl) : dayPnl === null ? "—" : signedMoney(dayPnl)}</p>
          {risk?.day_floor?.armed ? <FloorMark closed={Boolean(risk.day_floor.closed)} amount={risk.day_floor.floor} /> : null}
        </div>
        <p className="desk-hero-note">{showLast ? "השוק סגור. זה יום המסחר האחרון." : narrative}</p>
      </header>

      <div className="ops-stage">
          <section className="ops-pane stage">
            <header className="ops-pane-head">
              <h2>פוזיציות פתוחות</h2>
              <span>{positions === null ? "טוען" : `${positions.length} פתוחות`}</span>
            </header>
            <div className="desk-blotter">
              {positions && positions.length > 0 ? positions.map((item) => (
                <button key={item.symbol} type="button" className="desk-pos" onClick={() => setPicked(openTrade(today, item.symbol) ?? fromPosition(item))}>
                  <b>{item.underlying || "—"}</b>
                  <span>{item.right || "—"} · {contracts(item.qty)}</span>
                  <span className="num">{price(item.avg_entry_price)} → {price(item.current_price)}</span>
                  <strong className={`num ${tone(item.unrealized_pl ? Number(item.unrealized_pl) : null)}`}>
                    {item.current_price ? money(item.unrealized_pl) : "—"}
                    <small>{item.current_price && item.unrealized_plpc ? share(Number(item.unrealized_plpc) * 100) : ""}</small>
                  </strong>
                </button>
              )) : (
                <p className="desk-empty">{positions === null ? "טוען" : "אין פוזיציה פתוחה"}</p>
              )}
            </div>
          </section>
      </div>

      <section className="desk-days">
        <header className="ops-pane-head">
          <h2>ימים</h2>
          <a href="/history">כל העסקאות</a>
        </header>
        <div className="desk-day-row">
          {days === null ? <p className="desk-empty">טוען</p> : days.length === 0 ? <p className="desk-empty">עוד אין יום מסחר סגור</p> : days.map((group) => (
            <a key={group.day} className="desk-day" href="/history">
              <span>{dayTitle(group.day)}</span>
              <b className={`num ${tone(group.pnl)}`}>{signedMoney(group.pnl)}</b>
              <small className={`num ${tone(group.pnl)}`}>{group.pct === null ? "—" : `${group.pct > 0 ? "+" : ""}${group.pct.toFixed(1)}%`}</small>
              <small>{group.wins} מתוך {group.trades} ברווח</small>
            </a>
          ))}
        </div>
      </section>

      <details className="desk-system">
        <summary>המערכת</summary>
      <div className="desk-stats">
        <ResultStat label="סגור" book={dayBook(today?.summary?.realized_pnl, today?.summary?.invested_closed)} empty={today === null ? "טוען" : "אין סגירה"} />
        <ResultStat label="פתוח" book={dayBook(today?.summary?.unrealized_pnl, today?.summary?.open_invested)} empty={today === null ? "טוען" : "אין פוזיציה"} />
        <Stat label="הון" value={money(account?.equity)} />
        <Stat label="מזומן" value={money(account?.cash)} />
        <Stat label="בסיכון" value={stake === null ? "—" : money(stake.dollars)} />
      </div>
      <div className="ops-meters">
        <Meter label="הפסד יומי" used={risk?.daily_loss_used} limit={risk?.daily_loss_limit} />
        <Meter label="חשיפה" used={exposure === null ? null : String(exposure)} limit={risk?.exposure_limit} />
        <Meter label="פוזיציות" used={risk?.open_positions == null ? null : String(risk.open_positions)} limit={risk ? String(risk.max_positions) : null} plain />
      </div>
      <section className="ops-pane stage">
        <header className="ops-pane-head">
          <h2>זרם פעילות היום</h2>
          <span>{todayLine(today)}</span>
        </header>
        <div className="ops-scroll">
          {flowOf(today, scan).length === 0 ? (
            <div className="ops-feed"><span>{today === null ? "טוען" : "עוד לא היו היום כניסות או יציאות"}</span></div>
          ) : flowOf(today, scan).map((event, index) => (
            <div
              className={`ops-flow ${event.kind}${event.trade ? " ops-pick" : ""}`}
              key={`${event.at}-${event.kind}-${index}`}
              title={event.trade ? "לחץ להסבר על העסקה" : undefined}
              onClick={() => event.trade && setPicked(event.trade)}
            >
              <b className="num ops-flow-time">{clock(event.at)}</b>
              <i>{event.kind === "entry" ? "כניסה" : event.kind === "exit" ? "יציאה" : "נחסם"}</i>
              <span className="ops-flow-copy">
                <bdi dir="ltr">{event.title}</bdi>
                {event.lines.map((line) => <em key={line}>{line}</em>)}
              </span>
              {event.kind === "exit" && event.trade ? (
                <b className={`num ops-flow-money ${tone(Number(event.trade.pnl))}`}>{money(event.trade.pnl)} · {share(event.trade.return_pct)}</b>
              ) : event.kind === "entry" && event.trade ? (
                <b className="num ops-flow-money">{money(event.trade.invested)}</b>
              ) : null}
            </div>
          ))}
        </div>
      </section>
      <div className="ops-below">
          <section className="ops-pane">
            <h2>צינור המערכת</h2>
            <div className="ops-rail">
              <Node label="סריקה אחרונה" value={scanNode(scan, closed)} />
              <Node label="משך סריקה" value={durationOf(scan)} />
              <Node label="סריקה רצה" value={runningOf(desk?.scan_progress ?? null)} />
              <Node label="AI" value={aiNode(scan, closed)} />
              <Node label="החלטות" value={scan ? String(scan.recommendations ?? 0) : closed ? "ממתין" : "אין נתונים"} />
              <Node label="BUY" value={scan ? String(scan.buys ?? 0) : closed ? "ממתין" : "אין נתונים"} />
              <Node label="נשלחו לברוקר" value={scan?.stages?.orders != null ? String(scan.stages.orders) : closed ? "ממתין" : "—"} />
              <Node label="פקודות פתוחות" value={orders === null ? "אין נתונים" : String(orders.length)} />
            </div>
          </section>
          <section className="ops-pane">
            <h2>לוח מצב</h2>
            <div className="ops-matrix">
              <Cell label="פתיחת סשן" value={clock(status?.session_open)} />
              <Cell label="סגירת סשן" value={clock(status?.session_close)} />
              <Cell label="מסד" value={status?.database === "ok" ? "פעיל" : status ? "אין חיבור" : "אין נתונים"} />
              <Cell label="מודל" value={status?.ai_model || "אין נתונים"} />
              <Cell label="הון לעסקה" value={money(risk?.per_trade_limit)} />
              <Cell label="תקרת הפסד" value={money(risk?.daily_loss_limit)} />
              <Cell label="תקרת חשיפה" value={money(risk?.exposure_limit)} />
              <Cell label="רווח/הפסד מההתחלה" value={totals ? money(totals.total_pnl) : "אין נתונים"} className={tone(totals ? Number(totals.total_pnl) : null)} />
              <Cell label="תשואה על הכסף שהושקע" value={totals ? share(totals.total_return_pct) : "אין נתונים"} className={tone(totals?.total_return_pct ? Number(totals.total_return_pct) : null)} />
              <Cell label="עלות AI מההתחלה" value={totals?.ai_cost != null ? money(totals.ai_cost) : "אין נתונים"} />
              <Cell label="נטו אחרי AI" value={totals?.net_after_ai != null ? money(totals.net_after_ai) : "אין נתונים"} className={tone(totals?.net_after_ai ? Number(totals.net_after_ai) : null)} />
            </div>
          </section>
          <section className="ops-pane">
            <h2>ערוצי נתונים</h2>
            <Feed label="שוק" row={feedOf(status?.market_status, status?.providers.market, status?.market_status?.quote_count ?? 0)} />
            <Feed label="אופציות" row={feedOf(status?.options_status, status?.providers.options, status?.options_status?.option_count ?? status?.options_status?.quote_count ?? 0)} />
            <Feed label="חדשות" row={newsOf(status)} />
            <Feed label="FRED" row={macroOf(status)} />
            <Feed label="SEC" row={secOf(status)} />
            <Feed label="AI" row={aiOf(status)} />
          </section>

          <section className="ops-pane">
            <h2>סיכון · {risk?.state || "אין נתונים"}</h2>
            <div className="ops-feed"><span>הון לעסקה</span><b className="num">{money(risk?.per_trade_limit)}</b></div>
            <div className="ops-feed"><span>מגבלה יומית</span><b className="num">{money(risk?.daily_loss_limit)}</b></div>
            <div className="ops-feed"><span>עלות AI היום</span><b className="num">{status ? `$${status.ai_day_usd}` : "אין נתונים"}</b></div>
            <div className="ops-feed"><span>עלות AI החודש</span><b className="num">{status ? `$${status.ai_month_usd}` : "אין נתונים"}</b></div>
            <div className="ops-feed"><span>קריאות החודש</span><b className="num">{status ? String(status.ai_calls) : "אין נתונים"}</b></div>
          </section>

          <section className="ops-pane grow">
            <h2>התראות וביקורת</h2>
            <div className="ops-scroll">
              {(status?.alerts || []).length === 0 ? <div className="ops-feed"><span>אין התראות פעילות</span></div> : status?.alerts.map((item) => (
                <div className="ops-feed" key={item}><span>{item}</span></div>
              ))}
              {(audit || []).slice(0, 6).map((item, index) => (
                <div className="ops-feed" key={`${item.created_at}-${index}`}>
                  <span className="num">{item.state}</span>
                  <b className="num">{clock(item.created_at)}</b>
                </div>
              ))}
              {audit && audit.length === 0 ? <div className="ops-feed"><span>אין רשומות ביקורת</span></div> : null}
            </div>
          </section>
      </div>
      </details>
      {picked ? <TradeWindow trade={picked} onClose={() => setPicked(null)} /> : null}
    </section>
  );
}

function TradeWindow({ trade, onClose }: { trade: FlowTrade; onClose: () => void }) {
  const closed = Boolean(trade.closed_at);
  const result = closed ? trade.pnl : trade.unrealized_pnl;
  const value = result == null || result === "" ? null : Number(result);
  return (
    <div className="engine-backdrop" onClick={onClose}>
      <article className="engine-modal" role="dialog" aria-modal="true" aria-labelledby="dash-trade-title" onClick={(event) => event.stopPropagation()}>
        <header>
          <div>
            <p>{closed ? "עסקה סגורה" : "פוזיציה פתוחה"} · PAPER · נפתחה {clock(trade.opened_at)}{closed ? ` · נסגרה ${clock(trade.closed_at)}` : ""}</p>
            <h2 id="dash-trade-title" className={tone(value)}>
              {contractLabel(trade)} · {value === null ? "—" : money(value)}{trade.return_pct ? ` (${share(trade.return_pct)})` : ""}
            </h2>
          </div>
          <button type="button" onClick={onClose}>סגור</button>
        </header>
        <TradeStory story={trade.story} />
        <p className="trade-story-foot">
          כניסה {price(trade.entry_price)} · {closed ? `יציאה ${price(trade.exit_price)}` : `עכשיו ${price(trade.current_price)}`} · הושקע {money(trade.invested)} · <a href="/history">כל העסקאות</a>
        </p>
      </article>
    </div>
  );
}

function openTrade(today: TodayReport | null, symbol: string) {
  const key = symbol.replace(/\s/g, "");
  return today?.open?.find((item) => item.symbol.replace(/\s/g, "") === key) ?? null;
}

function fromPosition(item: Position): FlowTrade {
  return {
    symbol: item.symbol,
    underlying: item.underlying,
    right: item.right,
    qty: item.qty,
    entry_price: item.avg_entry_price,
    current_price: item.current_price,
    invested: item.cost_basis,
    unrealized_pnl: item.unrealized_pl,
    story: null,
  };
}

function Cell({ label, hint, value, detail, className = "" }: { label: string; hint?: string; value: string; detail?: string; className?: string }) {
  return (
    <div className="ops-cell">
      <span>{label}{hint ? <em> {hint}</em> : null}</span>
      <b className={className}>{value}</b>
      {detail ? <small>{detail}</small> : null}
    </div>
  );
}

function Stat({ label, value, className = "" }: { label: string; value: string; className?: string }) {
  return (
    <div className="desk-stat">
      <span>{label}</span>
      <b className={`num ${className}`}>{value}</b>
    </div>
  );
}

function ResultStat({ label, book, empty }: { label: string; book: DayBook | null; empty: string }) {
  if (book === null) return <Stat label={label} value={empty} />;
  return <Stat label={label} value={signedMoney(book.profit)} className={tone(book.profit)} />;
}

const WEEKDAYS = ["ראשון", "שני", "שלישי", "רביעי", "חמישי", "שישי", "שבת"];

function dayTitle(day: string) {
  const [year, month, date] = day.split("-").map(Number);
  if (!year || !month || !date) return day;
  const weekday = WEEKDAYS[new Date(Date.UTC(year, month - 1, date)).getUTCDay()] ?? "";
  return `${weekday} ${String(date).padStart(2, "0")}/${String(month).padStart(2, "0")}`;
}

function recentDays(rows: ClosedLite[] | null) {
  if (rows === null) return null;
  const groups = new Map<string, { day: string; pnl: number; invested: number; trades: number; wins: number }>();
  for (const row of rows) {
    const profit = num(row.pnl);
    if (!row.closed_at || profit === null) continue;
    const day = new Intl.DateTimeFormat("en-CA", { timeZone: "America/New_York", year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date(row.closed_at));
    const group = groups.get(day) ?? { day, pnl: 0, invested: 0, trades: 0, wins: 0 };
    group.pnl += profit;
    group.invested += num(row.invested) ?? 0;
    group.trades += 1;
    if (profit > 0) group.wins += 1;
    groups.set(day, group);
  }
  return [...groups.values()]
    .sort((a, b) => (a.day < b.day ? 1 : -1))
    .slice(0, 8)
    .map((group) => ({ ...group, pct: group.invested > 0 ? (group.pnl / group.invested) * 100 : null }));
}

function ResultCell({ label, book, empty }: { label: string; book: DayBook | null; empty: string }) {
  if (book === null) return <Cell label={label} value={empty} />;
  return <Cell label={label} value={signedMoney(book.profit)} detail={`${share(book.pct)} על ${money(book.invested)}`} className={tone(book.profit)} />;
}

function dayBook(pnl: string | null | undefined, invested: string | null | undefined): DayBook | null {
  const profit = num(pnl);
  const base = num(invested);
  if (profit === null || base === null || base <= 0) return null;
  return { profit, invested: base, pct: (profit / base) * 100 };
}

function stakeOf(open: FlowTrade[] | undefined, positions: Position[] | null): { dollars: number; note: string } | null {
  const rows = open ?? [];
  const live = rows
    .map((row) => {
      const now = num(row.unrealized_pnl);
      const atStop = num(row.levels?.pnl_at_active_exit);
      if (now === null || atStop === null) return null;
      return Math.max(0, now - atStop);
    })
    .filter((value): value is number => value !== null);
  if (rows.length > 0 && live.length === rows.length) {
    return { dollars: live.reduce((sum, value) => sum + value, 0), note: "מהמחיר עכשיו עד הסטופ הפעיל" };
  }
  const planned = rows.map((row) => num(row.plan?.planned_risk)).filter((value): value is number => value !== null);
  if (rows.length > 0 && planned.length === rows.length) {
    return { dollars: planned.reduce((sum, value) => sum + value, 0), note: "ההפסד המתוכנן מהכניסה" };
  }
  if (positions === null) return null;
  const premium = positions.reduce((sum, item) => sum + Math.abs(num(item.cost_basis) ?? 0), 0);
  return { dollars: premium, note: rows.length === 0 && positions.length === 0 ? "אין פוזיציה פתוחה" : "הפרמיה ששולמה, בלי רמת סטופ" };
}

function num(value: string | number | null | undefined) {
  if (value === null || value === undefined || value === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function signedMoney(value: number) {
  return `${value > 0 ? "+" : value < 0 ? "-" : ""}${money(Math.abs(value))}`;
}

type DayBook = { profit: number; invested: number; pct: number };

function Node({ label, value }: { label: string; value: string }) {
  const waiting = value.startsWith("ממתין");
  return (
    <div className={`ops-node${waiting ? " wait" : ""}`}>
      <span>{label}</span>
      <b className="num">{value}</b>
    </div>
  );
}

function syncOf(syncedAt: number | null, now: number) {
  if (syncedAt == null) return { text: "מתחבר", tone: "warn" };
  const seconds = Math.max(0, Math.round((now - syncedAt) / 1000));
  if (seconds <= 20) return { text: "חי", tone: "on" };
  return { text: `עודכן לפני ${ageLabel(seconds)}`, tone: seconds > 90 ? "off" : "warn" };
}

function liveAge(base: number | null | undefined, beatAt: number | null, now: number) {
  if (base == null || beatAt == null) return base;
  return base + Math.max(0, (now - beatAt) / 1000);
}

function Meter({ label, used, limit, plain = false }: { label: string; used?: string | null; limit?: string | null; plain?: boolean }) {
  const usedNumber = used == null || used === "" ? null : Number(used);
  const limitNumber = limit == null || limit === "" ? null : Number(limit);
  const ratio = usedNumber !== null && limitNumber && Number.isFinite(usedNumber) && Number.isFinite(limitNumber) && limitNumber > 0
    ? Math.min(100, Math.max(0, (usedNumber / limitNumber) * 100))
    : null;
  const shown = (value: string | null | undefined) => (plain ? (value ?? "אין נתונים") : money(value));
  return (
    <div className="ops-meter">
      <header>
        <span>{label}</span>
        <b className="num">{ratio === null ? "אין נתונים" : `${shown(used)} / ${shown(limit)}`}</b>
      </header>
      <div className="ops-track"><i style={{ width: `${ratio ?? 0}%` }} /></div>
    </div>
  );
}

function Feed({ label, row }: { label: string; row: { text: string; tone: string } }) {
  return (
    <div className="ops-feed">
      <span>{label}</span>
      <b className={row.tone === "ok" ? "up" : row.tone === "bad" ? "down" : "flat"}>{row.text}</b>
    </div>
  );
}

function exposureOf(items: Position[] | null) {
  if (items === null) return null;
  if (items.length === 0) return 0;
  const values = items.map((item) => item.market_value).filter((value): value is string => value !== null && value !== undefined && value !== "");
  if (values.length !== items.length) return null;
  const total = values.reduce((sum, value) => sum + Math.abs(Number(value)), 0);
  return Number.isFinite(total) ? total : null;
}

function price(value: string | null | undefined) {
  if (value == null || value === "") return "—";
  const number = Number(value);
  return Number.isFinite(number) ? number.toFixed(2) : "—";
}

function contracts(value: number | string | null | undefined) {
  const parsed = num(value);
  if (parsed === null) return "—";
  return Number.isInteger(parsed) ? String(parsed) : parsed.toLocaleString("en-US", { maximumFractionDigits: 2 });
}

function clauses(reason: string) {
  return reason.split(";").map((part) => part.trim()).filter(Boolean);
}

function contractLabel(trade: FlowTrade) {
  const side = trade.right === "PUT" ? "PUT" : trade.right === "CALL" ? "CALL" : "";
  return `${trade.underlying || trade.symbol} ${side}`.trim();
}

function openedAt(today: TodayReport | null, symbol: string) {
  return openTrade(today, symbol)?.opened_at ?? null;
}

function flowOf(today: TodayReport | null, scan: Desk["last_scan"]): FlowEvent[] {
  const events: FlowEvent[] = [];
  const seen = new Set<string>();
  for (const trade of [...(today?.closed ?? []), ...(today?.open ?? [])]) {
    const entryKey = `${trade.symbol}-${trade.opened_at}`;
    if (trade.opened_at && !seen.has(entryKey)) {
      seen.add(entryKey);
      events.push({
        at: trade.opened_at,
        kind: "entry",
        trade,
        title: contractLabel(trade),
        lines: [`${contracts(trade.qty_open ?? trade.qty)} חוזים`, `כניסה ${price(trade.entry_price)}`],
      });
    }
    if (trade.closed_at) {
      events.push({
        at: trade.closed_at,
        kind: "exit",
        trade,
        title: contractLabel(trade),
        lines: [`כניסה ${price(trade.entry_price)}`, `יציאה ${price(trade.exit_price)}`, exitLabel(trade.exit_reason)],
      });
    }
  }
  const blocked = (scan?.stages?.buy_outcomes ?? []).filter((item) => item.result === "blocked");
  if (blocked.length > 0 && scan?.finished_at) {
    for (const item of blocked) {
      events.push({
        at: scan.finished_at,
        kind: "blocked",
        trade: null,
        title: item.symbol.replace(/\s+/g, " "),
        lines: clauses(item.reason),
      });
    }
  }
  return events.sort((a, b) => new Date(b.at).getTime() - new Date(a.at).getTime());
}

function todayLine(today: TodayReport | null) {
  const summary = today?.summary;
  if (!summary) return "אין נתונים";
  const realized = Number(summary.realized_pnl);
  const sign = Number.isFinite(realized) && realized > 0 ? "+" : "";
  return `${summary.closed_count ?? 0} נסגרו · ${summary.wins ?? 0} ברווח · ${summary.losses ?? 0} בהפסד · ממומש ${sign}${money(summary.realized_pnl)}`;
}

function durationOf(scan: Desk["last_scan"]) {
  if (!scan?.observed_at || !scan.finished_at) return "—";
  const seconds = (new Date(scan.finished_at).getTime() - new Date(scan.observed_at).getTime()) / 1000;
  if (!Number.isFinite(seconds) || seconds < 0) return "—";
  return seconds < 60 ? `${Math.round(seconds)} שנ׳` : `${(seconds / 60).toFixed(1)} דק׳`;
}

const STAGE_TEXT: Record<string, string> = {
  started: "התחלה",
  universe: "יקום",
  underlyings: "מניות",
  options: "אופציות",
  news: "חדשות",
  macro: "מאקרו",
  research: "מחקר/AI",
  decisions: "החלטות",
  storage: "שמירה",
  submitting: "שליחה לברוקר",
};

function runningOf(progress: Desk["scan_progress"]) {
  if (!progress?.started_at || progress.stage_done === "done") return "לא";
  const minutes = (Date.now() - new Date(progress.started_at).getTime()) / 60000;
  if (!Number.isFinite(minutes) || minutes < 0) return "—";
  const stage = STAGE_TEXT[progress.stage_done ?? ""] ?? progress.stage_done ?? "";
  return `${minutes.toFixed(1)} דק׳ · אחרי ${stage}`;
}

function aiNode(scan: Desk["last_scan"], closed: boolean) {
  if (!scan) return closed ? "ממתין" : "אין נתונים";
  const failures = Object.values(scan.stages?.ai_failures ?? {}).reduce((sum, count) => sum + count, 0);
  return failures > 0 ? `${scan.ai_calls ?? 0} · ${failures} נכשלו` : String(scan.ai_calls ?? 0);
}

function scanNode(scan: Desk["last_scan"], closed: boolean) {
  if (scan?.blocked) return "חסום";
  if (scan?.observed_at) return clock(scan.observed_at);
  if (closed) return "ממתין לפתיחה";
  return "אין נתונים";
}

function narrativeOf(desk: Desk | null, scan: Desk["last_scan"], closed: boolean, phase: string) {
  if (!desk) return "מתחבר למנוע";
  if (!desk.autonomous) return "המנוע לא פעיל. אין סריקה חיה.";
  if (scan?.blocked) return `סריקה אחרונה נעצרה: ${scan.blocked}`;
  if (scan?.observed_at) {
    const outcomes = scan.stages?.buy_outcomes ?? [];
    const sent = outcomes.filter((item) => item.result === "submitted").length;
    const stopped = outcomes.filter((item) => item.result === "blocked");
    const why = stopped.length > 0 ? ` · ${stopped.length} נחסמו: ${stopped[0].reason}` : "";
    return `סריקה אחרונה ${clock(scan.observed_at)} · משך ${durationOf(scan)} · ${scan.buys ?? 0} BUY · ${sent} נשלחו${why}`;
  }
  if (closed) return `המנוע פועם. ${phase}. הסריקה ממתינה לחלון המסחר.`;
  return "המנוע פועם. אין עדיין סריקה שמורה.";
}

function feedOf(feed: SystemStatus["market_status"], name: string | undefined, count: number) {
  if (!name || name === "unconfigured") return { text: "לא מוגדר", tone: "warn" };
  if (!feed) return { text: "אין נתונים", tone: "wait" };
  if (feed.last_error) return { text: "תקלה", tone: "bad" };
  if (feed.last_success && count > 0) return { text: "יש נתון", tone: "ok" };
  if (feed.last_success) return { text: "אין ציטוט", tone: "wait" };
  return { text: "ממתין", tone: "wait" };
}

function newsOf(status: SystemStatus | null) {
  if (!status) return { text: "אין נתונים", tone: "wait" };
  const news = status.news_status;
  if (!news || status.providers.news === "unconfigured") return { text: "לא מוגדר", tone: "warn" };
  if (news.last_error) return { text: "תקלה", tone: "bad" };
  if (news.authenticated && news.last_success && news.article_count > 0) return { text: `${news.article_count} ידיעות`, tone: "ok" };
  if (news.authenticated) return { text: "מפתח שמור", tone: "wait" };
  return { text: "ממתין", tone: "wait" };
}

function macroOf(status: SystemStatus | null) {
  const macro = status?.macro_status;
  if (!macro) return { text: "אין נתונים", tone: "wait" };
  if (macro.last_error) return { text: "תקלה", tone: "bad" };
  if (macro.authenticated && macro.last_success && macro.series_count > 0) return { text: `${macro.series_count} סדרות`, tone: "ok" };
  return { text: "ממתין", tone: "wait" };
}

function secOf(status: SystemStatus | null) {
  const research = status?.research_status;
  if (!research) return { text: "אין נתונים", tone: "wait" };
  if (research.last_error) return { text: "תקלה", tone: "bad" };
  if (research.authenticated && research.last_success && research.filing_count > 0) return { text: `${research.filing_count} דיווחים`, tone: "ok" };
  return { text: "ממתין", tone: "wait" };
}

function aiOf(status: SystemStatus | null) {
  if (!status) return { text: "אין נתונים", tone: "wait" };
  if (status.ai_mode === "stopped") return { text: "עצור", tone: "bad" };
  if (status.ai_mode === "unpriced" || status.ai_mode === "soft" || status.ai_mode === "critical") return { text: "מוגבל", tone: "warn" };
  return { text: status.ai_model || "פעיל", tone: "ok" };
}
