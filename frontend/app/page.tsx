"use client";

import { useEffect, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { Shell, useDesk } from "@/components/Shell";
import { PHASES, holdScreen, loadCached, peekCached, poll, transportMode, type SystemStatus } from "@/lib/api";
import { TradeStory, type Story } from "@/components/TradeStory";
import { exitLabel } from "@/lib/exits";
import { FloorMark } from "@/components/FloorMark";
import { ProtectionBar, type Protection } from "@/components/ProtectionBar";
import { copy } from "@/lib/copy";
import { money as usd, parseStamp, sessionStamp, signedMoney as usdSigned, signedPct } from "@/lib/format";
import { regimeLine } from "@/lib/regime";
import { Heartbeat, KillBanner, Num, Odometer, Radar, Sparkline, StaleNote, useReduced, useTick } from "@/components/live-ui";
import { returnCaption, shownDayPnl } from "@/lib/protection";
import { observeTrades, useLive } from "@/lib/live";

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
  levels?: {
    available?: boolean;
    pnl_at_active_exit?: string | null;
    dte?: number | null;
    stop?: string | null;
    entry?: string | null;
    current?: string | null;
    trail_on?: string | null;
    active_exit?: string | null;
    stage?: string | null;
  } | null;
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
  available?: boolean;
  stale?: boolean;
  as_of?: string | null;
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
type FlowFilter = "all" | "trades" | "blocks";
type AgedReport = { available?: boolean; stale?: boolean; as_of?: string | null; summary?: TradeTotals; closed?: ClosedLite[] };

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
  daily_kill_loss_usd?: string | null;
  daily_loss_used: string | null;
  daily_loss_state?: string | null;
  today_pnl?: string | null;
  display_pnl?: string | null;
  day_pnl_note?: string | null;
  day_return_pct?: string | null;
  day_invested_usd?: string | null;
  day_floor?: { armed: boolean; closed: boolean; arm: string; floor: string } | null;
  protection?: Protection | null;
  max_positions: number;
  open_positions: number | null;
};

type AuditRow = { state: string; reason: string; created_at: string };
type Order = { symbol?: string; internal_state?: string; qty?: string; position_intent?: string };

function money(value: string | number | null | undefined) {
  const number = typeof value === "number" ? value : value == null || value === "" ? Number.NaN : Number(value);
  if (!Number.isFinite(number)) return copy.common.noData;
  return usd(number);
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
  return signedPct(number, 2);
}

function tone(value: number | null) {
  if (value === null || value === 0) return "flat";
  return value > 0 ? "up" : "down";
}

function DayReturn({ pct, note }: { pct: string | null | undefined; note?: string | null }) {
  const caption = returnCaption(num(pct), note ?? null);
  return <p className={`desk-hero-return ${caption.tone}`}>{caption.text}</p>;
}

function clock(iso: string | null | undefined) {
  if (!iso) return "—";
  const date = parseStamp(iso);
  if (!date) return iso.slice(11, 19) || iso;
  return new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit", second: "2-digit", timeZone: "Asia/Dubai", hourCycle: "h23" }).format(date);
}

export default function HomePage() {
  return (
    <Shell>
      <Board />
    </Shell>
  );
}

function Board() {
  const { status } = useDesk();
  const live = useLive();
  const reduce = useReduced();
  const [desk, setDesk] = useState<Desk | null>(() => peekCached<Desk>("/api/v1/desk"));
  const [account, setAccount] = useState<BrokerAccount | null>(() => peekCached<{ account?: BrokerAccount }>("/api/v1/broker/alpaca/account")?.account ?? null);
  const [positions, setPositions] = useState<Position[] | null>(() => peekCached<{ items?: Position[] }>("/api/v1/broker/alpaca/positions")?.items ?? null);
  const [orders, setOrders] = useState<Order[] | null>(() => peekCached<{ items?: Order[] }>("/api/v1/broker/alpaca/orders?status=open")?.items ?? null);
  const [today, setToday] = useState<TodayReport | null>(() => peekCached<TodayReport>("/api/v1/report/trades?period=today"));
  const [picked, setPicked] = useState<FlowTrade | null>(null);
  const [audit, setAudit] = useState<AuditRow[] | null>(() => peekCached<{ items?: AuditRow[] }>("/api/v1/audit")?.items ?? null);
  const [risk, setRisk] = useState<Risk | null>(() => peekCached<Risk>("/api/v1/risk"));
  const [totals, setTotals] = useState<TradeTotals | null>(() => peekCached<AgedReport>("/api/v1/report/trades?period=all")?.summary ?? null);
  const [book, setBook] = useState<ClosedLite[] | null>(() => peekCached<AgedReport>("/api/v1/report/trades?period=all")?.closed ?? null);
  const [bookAge, setBookAge] = useState<{ stale?: boolean; as_of?: string | null }>(() => {
    const cached = peekCached<AgedReport>("/api/v1/report/trades?period=all");
    return { stale: cached?.stale, as_of: cached?.as_of };
  });
  const [flowFilter, setFlowFilter] = useState<FlowFilter>("trades");
  const [syncedAt, setSyncedAt] = useState<number | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const [beatAt, setBeatAt] = useState<number | null>(null);

  useEffect(() => {
    let alive = true;
    const feed = <T,>(path: string, apply: (body: T) => void, everyMs: number, timeoutMs = 15000) =>
      poll(async () => {
        await loadCached<T>(path, (body) => {
          if (!alive) return;
          apply(body);
          setSyncedAt(Date.now());
        }, timeoutMs);
      }, everyMs);
    const stops = [
      feed<Desk>("/api/v1/desk", setDesk, 15000),
      feed<{ items?: Position[] }>("/api/v1/broker/alpaca/positions", (body) => {
        if (transportMode() === "sse") return;
        setPositions(body.items || []);
      }, 12000),
      feed<Risk>("/api/v1/risk", setRisk, 15000),
      feed<TodayReport>("/api/v1/report/trades?period=today", (body) => setToday((current) => holdScreen(current, body) ?? body), 60000, 30000),
      feed<{ items?: Order[] }>("/api/v1/broker/alpaca/orders?status=open", (body) => setOrders(body.items || []), 15000),
      feed<{ items: AuditRow[] }>("/api/v1/audit", (body) => setAudit(body.items || []), 30000),
      feed<{ account?: BrokerAccount }>("/api/v1/broker/alpaca/account", (body) => {
        if (transportMode() === "sse") return;
        setAccount(body.account || null);
      }, 15000),
      feed<AgedReport>("/api/v1/report/trades?period=all", (body) => {
        if (body.available === false) {
          setBookAge((current) => ({ stale: true, as_of: current.as_of ?? new Date().toISOString() }));
          return;
        }
        setTotals(body.summary ?? null);
        if (body.closed) setBook(body.closed);
        setBookAge({ stale: body.stale, as_of: body.as_of });
      }, 60000, 30000),
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

  useEffect(() => {
    if (today === null) return;
    observeTrades([...(today.closed ?? []), ...(today.open ?? [])]);
  }, [today]);

  const ssePositions = transportMode() === "sse" ? (live.snap?.positions?.items as Position[] | undefined) : undefined;
  const sseAccount = transportMode() === "sse" ? live.snap?.account : undefined;
  const viewPositions = ssePositions ?? positions;
  const viewAccount = (sseAccount as BrokerAccount | null | undefined) ?? account;
  const sse = transportMode() === "sse";
  const dayNote = (sse ? live.snap?.day?.day_pnl_note : null) ?? risk?.day_pnl_note ?? live.snap?.day?.day_pnl_note ?? null;
  const displayPnl = num(sse ? live.snap?.day?.display_pnl : undefined) ?? num(risk?.display_pnl) ?? num(live.snap?.day?.display_pnl);
  const dayPnl = num(live.snap?.day?.pnl) ?? num(risk?.today_pnl);
  const markPnl = num(live.snap?.day?.mark_pnl);
  const kill = num(live.snap?.day?.daily_kill_loss_usd) ?? num(risk?.daily_kill_loss_usd) ?? num(status?.daily_kill_loss_usd);
  const scan = desk?.last_scan ?? null;
  const exposure = exposureOf(viewPositions);
  const stake = stakeOf(today?.open, viewPositions);
  const phase = status ? PHASES[status.market_phase] || status.market_phase : "מתחבר";
  const closed = status?.market_phase === "CLOSED" || status?.market_phase === "POST_MARKET" || status?.market_phase === "DAILY_REPORT";
  const narrative = narrativeOf(desk, scan, closed, phase);
  const liveFloor = transportMode() === "sse" ? live.snap?.day?.day_floor : undefined;
  const floor = liveFloor ?? risk?.day_floor ?? live.snap?.day?.day_floor ?? null;
  const protection = (transportMode() === "sse" ? live.snap?.day?.protection : undefined) ?? risk?.protection ?? live.snap?.day?.protection ?? null;
  const dayState = transportMode() === "sse" ? live.snap?.day?.daily_loss_state : risk?.daily_loss_state;
  const lossUnknown = !dayNote && (dayState === "unknown" || (dayState !== "hard_stop" && protection?.status === "unknown"));
  const lossState = lossUnknown ? "unknown" : live.snap?.day?.daily_loss_state;
  const sync = syncOf(syncedAt, now);
  const days = recentDays(book);
  const lastDay = days?.[0] ?? null;
  const dayView = shownDayPnl({
    display: displayPnl,
    mark: markPnl,
    risk: dayPnl,
    note: dayNote,
    closed,
    lastDay: lastDay?.pnl ?? null,
  });
  const showLast = dayView.showLast;
  const shownPnl = dayView.pnl;

  const equityUp = (num(viewAccount?.equity) ?? 0) >= (num(viewAccount?.last_equity) ?? num(viewAccount?.equity) ?? 0);

  return (
    <section className="desk-home">
      <KillBanner pnl={shownPnl} kill={kill} state={dayView.note ? null : lossState} />
      <div className="desk-top">
        <article className="ops-pane account-card">
          <header className="ops-pane-head">
            <h2>{copy.dashboard.account}</h2>
            <span className="head-meta">
              <StaleNote stale={today?.stale} asOf={today?.as_of} now={now} />
              <span className={sync.tone === "on" ? "up" : sync.tone === "off" ? "down" : ""}>{live.stale ? copy.common.stale(Math.max(1, Math.round((now - (live.updatedAt || now)) / 1000))) : sync.text}</span>
            </span>
          </header>
          <div className="acct-pair">
            <ResultStat label={copy.dashboard.closed} book={dayBook(today?.summary?.realized_pnl, today?.summary?.invested_closed)} empty={today === null ? copy.common.loading : copy.dashboard.emptyFlow} />
            <ResultStat label={copy.dashboard.open} book={dayBook(today?.summary?.unrealized_pnl, today?.summary?.open_invested)} empty={today === null ? copy.common.loading : copy.dashboard.emptyPositions} />
          </div>
          <div className="acct-equity">
            <span>{copy.dashboard.equity}</span>
            <Num>{money(viewAccount?.equity)}</Num>
            <Sparkline points={live.series.eq || []} up={equityUp} />
          </div>
          <div className="acct-pair">
            <Stat label={copy.dashboard.cash} value={money(viewAccount?.cash)} />
            <Stat label={copy.dashboard.atRisk} value={stake === null ? "—" : money(stake.dollars)} />
          </div>
        </article>
        <article className="ops-pane desk-hero">
          <div className="desk-hero-top">
            <span>{phase}</span>
            <span>{showLast ? copy.dashboard.closedNote : narrative.short}</span>
          </div>
          <p className="desk-hero-label">{showLast && lastDay ? dayTitle(lastDay.day) : copy.dashboard.today}</p>
          <div className="desk-hero-main">
            <div className="desk-hero-figure">
              <Odometer value={shownPnl} />
              {showLast ? null : <DayReturn pct={sse ? live.snap?.day?.day_return_pct : risk?.day_return_pct ?? live.snap?.day?.day_return_pct} note={dayView.note} />}
            </div>
            {floor?.armed && floor.floor ? <FloorMark closed={Boolean(floor.closed)} amount={String(floor.floor)} /> : lossUnknown ? <span className="floor-mark unknown" aria-label="לא ידוע"><b>לא ידוע</b></span> : null}
          </div>
          {showLast ? null : protection || lossUnknown ? <ProtectionBar protection={lossUnknown ? { status: "unknown", line: "לא ידוע" } : protection} /> : null}
        </article>
      </div>

      <section className="ops-pane">
        <header className="ops-pane-head">
          <h2>{copy.dashboard.positions}</h2>
          <span>{viewPositions === null ? copy.common.loading : copy.dashboard.openCount(viewPositions.length)}</span>
        </header>
        {viewPositions && viewPositions.length > 0 ? <div className="pos-head" aria-hidden="true"><span>שם</span><span>DTE</span><span>חוזים</span><span>כניסה → עכשיו</span><span /><span>סטופ</span><span>רווח/הפסד</span></div> : null}
        <div className="desk-blotter blot">
          <AnimatePresence initial={false}>
          {viewPositions && viewPositions.length > 0 ? viewPositions.map((item, index) => (
            <PosRow
              key={item.symbol}
              item={item}
              trade={openTrade(today, item.symbol)}
              index={index}
              reduce={reduce}
              points={live.series[item.symbol] || []}
              onOpen={() => setPicked(openTrade(today, item.symbol) ?? fromPosition(item))}
            />
          )) : (
            <p className="desk-empty">{viewPositions === null ? copy.common.loading : copy.dashboard.emptyPositions}</p>
          )}
          </AnimatePresence>
        </div>
      </section>

      <div className="desk-split">
        <section className="ops-pane">
          <header className="ops-pane-head">
            <h2>{copy.dashboard.flow}</h2>
            <span className="head-meta">
              <StaleNote stale={today?.stale} asOf={today?.as_of} now={now} />
              <span>{todayLine(today)}</span>
            </span>
          </header>
          <div className="flow-switch" role="tablist" aria-label="סינון פעילות">
            {([
              ["all", "הכל"],
              ["trades", "עסקאות"],
              ["blocks", "חסימות"],
            ] as const).map(([id, label]) => (
              <button key={id} type="button" role="tab" aria-selected={flowFilter === id} className={flowFilter === id ? "on" : ""} onClick={() => setFlowFilter(id)}>
                {label}
              </button>
            ))}
          </div>
          <div className="flow-scroll">
          <div className="ops-scroll lux-scroll">
            {shownFlow(today, scan, flowFilter).length === 0 ? (
              <div className="ops-feed"><span>{flowEmpty(today, scan, flowFilter)}</span></div>
            ) : shownFlow(today, scan, flowFilter).map((event, index) => (
              <div
                className={`ops-flow ${event.kind}${event.trade ? " ops-pick" : ""}`}
                key={`${event.at}-${event.kind}-${index}`}
                title={event.trade ? narrative.detail : undefined}
                onClick={() => event.trade && setPicked(event.trade)}
              >
                <b className="num ops-flow-time">{clock(event.at)}</b>
                <i>{event.kind === "entry" ? copy.dashboard.entry : event.kind === "exit" ? copy.dashboard.exit : copy.dashboard.blocked}</i>
                <FlowCopy event={event} />
                {event.kind === "exit" && event.trade ? (
                  <b className={`num ops-flow-money ${tone(Number(event.trade.pnl))}`}>{money(event.trade.pnl)} · {share(event.trade.return_pct)}</b>
                ) : event.kind === "entry" && event.trade ? (
                  <b className="num ops-flow-money">{money(event.trade.invested)}</b>
                ) : null}
              </div>
            ))}
          </div>
          </div>
        </section>
        <section className="ops-pane desk-days">
          <header className="ops-pane-head">
            <h2>{copy.dashboard.days}</h2>
            <span className="head-meta">
              <StaleNote stale={bookAge.stale} asOf={bookAge.as_of} now={now} />
              <a href="/history">{copy.dashboard.allTrades}</a>
            </span>
          </header>
          <div className="day-scroll">
          <div className="desk-day-row lux-scroll">
            {days === null ? <p className="desk-empty">{copy.common.loading}</p> : days.length === 0 ? <p className="desk-empty">{copy.dashboard.emptyDays}</p> : days.map((group) => (
              <a key={group.day} className="desk-day" href="/history">
                <span>{dayTitle(group.day)}</span>
                <Num className={tone(group.pnl)}>{signedMoney(group.pnl)}</Num>
                <small><Num className={tone(group.pnl)}>{group.pct === null ? "—" : signedPct(group.pct, 1)}</Num></small>
                <small>{copy.dashboard.winsOf(group.wins, group.trades)}</small>
              </a>
            ))}
          </div>
          </div>
        </section>
      </div>

      <div className="ops-meters">
        <Meter label={copy.dashboard.dailyLoss} used={risk?.daily_loss_used} limit={kill == null ? risk?.daily_loss_limit : String(kill)} />
        <Meter label={copy.dashboard.exposure} used={exposure === null ? null : String(exposure)} limit={risk?.exposure_limit} />
        <Meter label={copy.dashboard.positionMeter} used={risk?.open_positions == null ? null : String(risk.open_positions)} limit={risk ? String(risk.max_positions) : null} plain />
      </div>

      <div className="desk-trio">
        <section className="ops-pane">
          <div className="pipe-head">
            <h2>{copy.dashboard.pipeline}</h2>
            <Radar names={live.snap?.scan?.names || []} active={Boolean(desk?.scan_progress && desk.scan_progress.stage_done !== "done")} />
          </div>
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
          <h2>{copy.dashboard.feeds}</h2>
          <Feed label="שוק" row={feedOf(status?.market_status, status?.providers.market, status?.market_status?.quote_count ?? 0)} />
          <Feed label="אופציות" row={feedOf(status?.options_status, status?.providers.options, status?.options_status?.option_count ?? status?.options_status?.quote_count ?? 0)} />
          <Feed label="חדשות" row={newsOf(status)} />
          <Feed label="FRED" row={macroOf(status)} />
          <Feed label="SEC" row={secOf(status)} />
          <Feed label="AI" row={aiOf(status)} />
          <Feed label="משטר" row={regimeOf(status, now)} />
        </section>
        <section className="ops-pane">
          <h2>סיכון · {lossUnknown ? "לא ידוע" : (risk?.state || "אין נתונים")}</h2>
          <div className="ops-feed"><span>הון לעסקה</span><b className="num">{money(risk?.per_trade_limit)}</b></div>
          <div className="ops-feed"><span>מגבלה יומית</span><b className="num">{money(risk?.daily_loss_limit)}</b></div>
          <div className="ops-feed"><span>בלם</span><b className="num">{kill === null ? "—" : money(kill)}</b></div>
          <div className="ops-feed"><span>פתיחה</span><b className="num">{clock(status?.session_open)}</b></div>
          <div className="ops-feed"><span>סגירה</span><b className="num">{clock(status?.session_close)}</b></div>
          <div className="ops-feed"><span>עלות AI היום</span><b className="num">{status ? money(status.ai_day_usd) : "אין נתונים"}</b></div>
          <div className="ops-feed"><span>מההתחלה</span><b className={`num ${tone(totals ? Number(totals.total_pnl) : null)}`}>{totals ? money(totals.total_pnl) : "אין נתונים"}</b></div>
          {(status?.alerts || []).slice(0, 2).map((item) => (
            <div className="ops-feed" key={item}><span>{item}</span></div>
          ))}
          {(audit || []).slice(0, 2).map((item, index) => (
            <div className="ops-feed" key={`${item.created_at}-${index}`}>
              <span className="num">{item.state}</span>
              <b className="num">{sessionStamp(item.created_at, true)}</b>
            </div>
          ))}
        </section>
      </div>
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

function Stat({ label, value, className = "" }: { label: string; value: string; className?: string }) {
  return (
    <div className="desk-stat">
      <span>{label}</span>
      <Num className={className}>{value}</Num>
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
    .map((group) => ({ ...group, pct: group.invested > 0 ? (group.pnl / group.invested) * 100 : null }));
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
  return usdSigned(value, 2);
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
      <Heartbeat live={row.tone === "ok"} />
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

function FlowCopy({ event }: { event: FlowEvent }) {
  const trade = event.trade;
  if (!trade) {
    return (
      <span className="ops-flow-copy">
        <span className="flow-bit"><bdi dir="ltr">{event.title}</bdi></span>
        {event.lines.map((line) => (
          <span className="flow-bit" key={line}>
            <bdi className="flow-sep">·</bdi>
            <bdi>{line}</bdi>
          </span>
        ))}
      </span>
    );
  }
  const side = trade.right === "CALL" || trade.right === "PUT" ? trade.right : "";
  return (
    <span className="ops-flow-copy">
      <span className="flow-bit"><Num>{trade.underlying || trade.symbol}</Num></span>
      {side ? <span className="flow-bit"><bdi className="flow-sep">·</bdi><bdi dir="ltr">{side}</bdi></span> : null}
      <span className="flow-bit"><bdi className="flow-sep">·</bdi><Num>{contracts(trade.qty_open ?? trade.qty)}</Num><bdi>חוזים</bdi></span>
      <span className="flow-bit"><bdi className="flow-sep">·</bdi><bdi>כניסה</bdi><Num>{price(trade.entry_price)}</Num></span>
      {event.kind === "exit" ? (
        <>
          <span className="flow-bit"><bdi className="flow-sep">·</bdi><bdi>יציאה</bdi><Num>{price(trade.exit_price)}</Num></span>
          <span className="flow-bit"><bdi className="flow-sep">·</bdi><bdi>{exitLabel(trade.exit_reason)}</bdi></span>
        </>
      ) : null}
    </span>
  );
}

function shownFlow(today: TodayReport | null, scan: Desk["last_scan"], filter: FlowFilter): FlowEvent[] {
  const events = flowOf(today, scan);
  if (filter === "blocks") return events.filter((event) => event.kind === "blocked");
  if (filter === "trades") return events.filter((event) => event.kind !== "blocked");
  return events;
}

function flowEmpty(today: TodayReport | null, scan: Desk["last_scan"], filter: FlowFilter) {
  if (today === null) return copy.common.loading;
  if (filter === "blocks") return "אין חסימות";
  if (filter === "trades" && flowOf(today, scan).some((event) => event.kind === "blocked")) return "אין עסקאות היום";
  return copy.dashboard.emptyFlow;
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
  if (!desk) return { short: copy.dashboard.connecting, detail: copy.dashboard.connecting };
  if (!desk.autonomous) return { short: copy.dashboard.engineOff, detail: "המנוע לא פעיל. אין סריקה חיה." };
  if (scan?.blocked) return { short: copy.dashboard.scanHeld, detail: scan.blocked };
  if (scan?.observed_at) {
    const outcomes = scan.stages?.buy_outcomes ?? [];
    const sent = outcomes.filter((item) => item.result === "submitted").length;
    const stopped = outcomes.filter((item) => item.result === "blocked");
    const why = stopped.length > 0 ? ` · ${stopped.length} נחסמו` : "";
    return { short: copy.dashboard.scanLive, detail: `${clock(scan.observed_at)} · ${durationOf(scan)} · ${scan.buys ?? 0} BUY · ${sent} נשלחו${why}` };
  }
  if (closed) return { short: copy.dashboard.waitOpen, detail: `${copy.dashboard.engineOn}. ${phase}` };
  return { short: copy.dashboard.engineOn, detail: copy.dashboard.engineOn };
}

function PosRow({ item, trade, index, reduce, points, onOpen }: { item: Position; trade: FlowTrade | null; index: number; reduce: boolean | null; points: number[]; onOpen: () => void }) {
  const flash = useTick(item.current_price);
  const pnl = item.unrealized_pl ? Number(item.unrealized_pl) : null;
  const dte = trade?.levels?.dte;
  const side = item.right === "CALL" || item.right === "PUT" ? item.right : "";
  return (
    <motion.button
      layout={!reduce}
      type="button"
      className={`pos-row row ${flash === "up" ? "fu" : flash === "down" ? "fd" : ""}`}
      onClick={onOpen}
      initial={reduce ? false : { opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      exit={reduce ? undefined : { opacity: 0, x: -40 }}
      transition={{ duration: reduce ? 0 : 0.22, delay: reduce ? 0 : index * 0.04 }}
    >
      <b className="pos-name">{item.underlying || "—"}{side ? <small className={`chip ${side === "CALL" ? "call" : "put"}`}>{side}</small> : null}</b>
      <span className="pos-meta">
        <span className="pos-dte">{dte === null || dte === undefined ? "—" : `${dte}DTE`}</span>
        <span className="pos-qty">{contracts(item.qty)}</span>
      </span>
      <Num className="pos-px">{price(item.avg_entry_price)} → {price(item.current_price)}</Num>
      <Sparkline points={points} up={(pnl ?? 0) >= 0} />
      <MiniLadder levels={trade?.levels} />
      <strong className="pos-pnl">
        <Num className={tone(pnl)}>{item.current_price ? (pnl === null ? "—" : signedMoney(pnl)) : "—"}</Num>
        <small><Num className={tone(pnl)}>{item.current_price && item.unrealized_plpc ? share(Number(item.unrealized_plpc) * 100) : ""}</Num></small>
      </strong>
    </motion.button>
  );
}

function MiniLadder({ levels }: { levels: FlowTrade["levels"] }) {
  const stop = num(levels?.stop);
  const entry = num(levels?.entry);
  const trail = num(levels?.trail_on);
  const current = num(levels?.current);
  if (stop === null && trail === null) return <span className="pos-miss">—</span>;
  const marks = [stop, entry, current, trail].filter((value): value is number => value !== null);
  const min = Math.min(...marks);
  const max = Math.max(...marks);
  const span = max - min || 1;
  const at = (value: number) => `${((value - min) / span) * 100}%`;
  return (
    <span className="ladder" aria-hidden="true">
      <i className="bar" />
      {stop !== null ? <i className="mk stop" style={{ insetInlineStart: at(stop) }} /> : null}
      {entry !== null ? <i className="mk entry" style={{ insetInlineStart: at(entry) }} /> : null}
      {current !== null ? <i className="mk now" style={{ insetInlineStart: at(current) }} /> : null}
      {trail !== null ? <i className="mk trail" style={{ insetInlineStart: at(trail) }} /> : null}
    </span>
  );
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

function regimeOf(status: SystemStatus | null, now: number) {
  const regime = status?.regime_status;
  const text = regimeLine(regime, now);
  const live = regime?.status === "CALCULATED" && Boolean(regime.market_regime);
  return { text, tone: live ? "ok" : "wait" };
}
