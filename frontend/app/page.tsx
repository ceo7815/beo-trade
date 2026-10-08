"use client";

import { useEffect, useState } from "react";
import { Shell } from "@/components/Shell";
import { PHASES, apiGet, type SystemStatus } from "@/lib/api";
import { TradeStory, type Story } from "@/components/TradeStory";
import { exitLabel } from "@/lib/exits";

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
};

type TodayReport = {
  summary?: { closed_count?: number; wins?: number; losses?: number; realized_pnl?: string; unrealized_pnl?: string; total_pnl?: string } | null;
  closed?: FlowTrade[];
  open?: FlowTrade[];
};

type FlowEvent = { at: string; kind: "entry" | "exit" | "blocked"; trade: FlowTrade | null; text: string };

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

function riskOf(positions: Position[] | null): { cost: number; pnl: number } | null {
  if (positions === null) return null;
  let cost = 0;
  let pnl = 0;
  for (const item of positions) {
    const basis = Number(item.cost_basis);
    const gain = Number(item.unrealized_pl);
    if (!item.current_price || !Number.isFinite(basis) || !Number.isFinite(gain)) return null;
    cost += Math.abs(basis);
    pnl += gain;
  }
  return { cost, pnl };
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
  const [missing, setMissing] = useState("");
  const [now, setNow] = useState(() => Date.now());
  const [beatAt, setBeatAt] = useState<number | null>(null);

  useEffect(() => {
    let alive = true;
    async function load() {
      const [deskBody, statusBody, accountBody, positionBody, orderBody, todayBody, auditBody, riskBody] = await Promise.allSettled([
        apiGet<Desk>("/api/v1/desk"),
        apiGet<SystemStatus>("/api/v1/system"),
        apiGet<{ account?: BrokerAccount }>("/api/v1/broker/alpaca/account"),
        apiGet<{ items?: Position[] }>("/api/v1/broker/alpaca/positions"),
        apiGet<{ items?: Order[] }>("/api/v1/broker/alpaca/orders?status=open"),
        apiGet<TodayReport>("/api/v1/report/trades?period=today"),
        apiGet<{ items: AuditRow[] }>("/api/v1/audit"),
        apiGet<Risk>("/api/v1/risk"),
      ]);
      if (!alive) return;
      if (deskBody.status === "fulfilled") setDesk(deskBody.value);
      if (statusBody.status === "fulfilled") setStatus(statusBody.value);
      if (accountBody.status === "fulfilled") {
        setAccount(accountBody.value.account || null);
        setMissing("");
      } else setMissing("אין נתוני חשבון");
      if (positionBody.status === "fulfilled") setPositions(positionBody.value.items || []);
      if (orderBody.status === "fulfilled") setOrders(orderBody.value.items || []);
      if (todayBody.status === "fulfilled") setToday(todayBody.value);
      if (auditBody.status === "fulfilled") setAudit(auditBody.value.items || []);
      if (riskBody.status === "fulfilled") setRisk(riskBody.value);
    }
    function loadTotals() {
      apiGet<{ summary?: TradeTotals }>("/api/v1/report/trades")
        .then((body) => alive && setTotals(body.summary ?? null))
        .catch(() => undefined);
    }
    load();
    loadTotals();
    const timer = window.setInterval(load, 12000);
    const totalsTimer = window.setInterval(loadTotals, 60000);
    const clockTimer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => {
      alive = false;
      window.clearInterval(totalsTimer);
      window.clearInterval(timer);
      window.clearInterval(clockTimer);
    };
  }, []);

  useEffect(() => {
    if (desk?.heartbeat_age_seconds != null) setBeatAt(Date.now());
  }, [desk?.heartbeat_age_seconds]);

  const equity = account?.equity ? Number(account.equity) : null;
  const pnl = risk?.today_pnl == null || risk.today_pnl === "" ? null : Number(risk.today_pnl);
  const scan = desk?.last_scan ?? null;
  const exposure = exposureOf(positions);
  const atRisk = riskOf(positions);
  const phase = status ? PHASES[status.market_phase] || status.market_phase : "מתחבר";
  const closed = status?.market_phase === "CLOSED" || status?.market_phase === "POST_MARKET" || status?.market_phase === "DAILY_REPORT";
  const narrative = narrativeOf(desk, scan, closed, phase);

  return (
    <section className="ops">
      <header className="ops-head">
        <div>
          <h1>לוח ראשי</h1>
          <p>מה המערכת עושה עכשיו</p>
        </div>
        <div className="ops-pips">
          <span className={`ops-pip ${desk ? (desk.autonomous ? "on" : "off") : "warn"}`}>
            <i />
            {desk ? (desk.autonomous ? "מנוע ON" : "מנוע OFF") : "מנוע"}
          </span>
          <span className="ops-pip">PAPER</span>
          <span className="ops-pip">Alpaca</span>
          <span className={`ops-pip ${status?.health === "ok" ? "on" : "warn"}`}>{status?.health === "ok" ? "פעיל" : status ? "מוגבל" : "מצב"}</span>
          <span className="ops-pip">פעימה {ageLabel(liveAge(desk?.heartbeat_age_seconds, beatAt, now))}</span>
          <span className="ops-pip">{phase}</span>
          <span className="ops-pip num">{status?.now_display || "—"}</span>
        </div>
      </header>

      <p className="ops-line">{narrative}</p>

      <div className="ops-book">
        <Cell label="הון עצמי" hint="Equity" value={money(account?.equity)} />
        <Cell label="מזומן" hint="Cash" value={money(account?.cash)} />
        <Cell label="כוח קנייה" hint="Buying Power" value={money(account?.buying_power)} />
        <Cell label="שווי תיק" hint="Portfolio" value={money(account?.portfolio_value)} />
        <Cell label="רווח/הפסד היום" hint="P&L" value={pnl === null ? "אין נתונים" : money(pnl)} className={tone(pnl)} />
        <Cell label="תשואה היום" hint="Return" value={pnl === null || !equity ? "אין נתונים" : `${((pnl / equity) * 100).toFixed(2)}%`} className={tone(pnl)} />
        <Cell label="כסף בסיכון" hint="Invested" value={atRisk === null ? "אין נתונים" : money(atRisk.cost)} />
        <Cell label="תשואה על הכסף בסיכון" hint="Return on Risk" value={atRisk === null ? "אין נתונים" : atRisk.cost > 0 ? share((atRisk.pnl / atRisk.cost) * 100) : "—"} className={tone(atRisk === null ? null : atRisk.pnl)} />
        <Cell label="פוזיציות" value={positions === null ? "אין נתונים" : String(positions.length)} />
        <Cell label="חשיפה" hint="Exposure" value={exposure === null ? "אין נתונים" : money(exposure)} />
      </div>

      <div className="ops-meters">
        <Meter label="הפסד יומי" used={risk?.daily_loss_used} limit={risk?.daily_loss_limit} />
        <Meter label="חשיפה מול מגבלה" used={exposure === null ? null : String(exposure)} limit={risk?.exposure_limit} />
        <Meter label="פוזיציות" used={risk?.open_positions == null ? null : String(risk.open_positions)} limit={risk ? String(risk.max_positions) : null} plain />
      </div>

      <div className="ops-grid">
        <div className="ops-main">
          <section className="ops-pane">
            <h2>פוזיציות פתוחות · Alpaca Paper · {positions === null ? "טוען" : `${positions.length} פתוחות`}</h2>
            <div className="ops-scroll">
              <table className="ops-table">
                <thead>
                  <tr>
                    <th>נכס</th>
                    <th>חוזה</th>
                    <th>צד</th>
                    <th>כמות</th>
                    <th>נפתחה</th>
                    <th>כניסה</th>
                    <th>עכשיו</th>
                    <th>הושקע</th>
                    <th>שווי</th>
                    <th>רווח/הפסד</th>
                    <th>%</th>
                  </tr>
                </thead>
                <tbody>
                  {positions && positions.length > 0 ? positions.map((item) => (
                    <tr key={item.symbol} className="ops-pick" title="לחץ להסבר על העסקה" onClick={() => setPicked(openTrade(today, item.symbol) ?? fromPosition(item))}>
                      <td className="num">{item.underlying || "—"}</td>
                      <td className="num">{item.symbol}</td>
                      <td className="num">{item.right || "—"}</td>
                      <td className="num">{item.qty || "—"}</td>
                      <td className="num">{clock(openedAt(today, item.symbol))}</td>
                      <td className="num">{price(item.avg_entry_price)}</td>
                      <td className="num">{price(item.current_price)}</td>
                      <td className="num">{money(item.cost_basis)}</td>
                      <td className="num">{money(item.market_value)}</td>
                      <td className={`num ${tone(item.unrealized_pl ? Number(item.unrealized_pl) : null)}`}>{item.current_price ? money(item.unrealized_pl) : "אין נתונים"}</td>
                      <td className={`num ${tone(item.unrealized_plpc ? Number(item.unrealized_plpc) : null)}`}>{item.current_price && item.unrealized_plpc ? share(Number(item.unrealized_plpc) * 100) : "—"}</td>
                    </tr>
                  )) : (
                    <tr><td colSpan={11}>{positions === null ? "טוען" : "אין פוזיציה פתוחה בברוקר"}</td></tr>
                  )}
                </tbody>
              </table>
            </div>
          </section>

          <section className="ops-pane grow">
            <h2>זרם פעילות היום · כניסות ויציאות · {todayLine(today)}</h2>
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
                  <b className="num">{clock(event.at)}</b>
                  <i>{event.kind === "entry" ? "כניסה" : event.kind === "exit" ? "יציאה" : "נחסם"}</i>
                  <span>{event.text}</span>
                  {event.kind === "exit" && event.trade ? (
                    <b className={`num ${tone(Number(event.trade.pnl))}`}>{money(event.trade.pnl)} · {share(event.trade.return_pct)}</b>
                  ) : event.kind === "entry" && event.trade ? (
                    <b className="num">{money(event.trade.invested)}</b>
                  ) : <b />}
                </div>
              ))}
            </div>
          </section>

          <section className="ops-pane">
            <h2>צינור המערכת</h2>
            <div className="ops-rail">
              <Node label="סריקה אחרונה" value={scanNode(scan, closed)} />
              <Node label="משך סריקה" value={durationOf(scan)} />
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
        </div>

        <aside className="ops-side">
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
        </aside>
      </div>
      {missing ? <p className="ops-line">{missing}</p> : null}
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

function Cell({ label, hint, value, className = "" }: { label: string; hint?: string; value: string; className?: string }) {
  return (
    <div className="ops-cell">
      <span>{label}{hint ? <em> {hint}</em> : null}</span>
      <b className={className}>{value}</b>
    </div>
  );
}

function Node({ label, value }: { label: string; value: string }) {
  const waiting = value.startsWith("ממתין");
  return (
    <div className={`ops-node${waiting ? " wait" : ""}`}>
      <span>{label}</span>
      <b className="num">{value}</b>
    </div>
  );
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
      events.push({ at: trade.opened_at, kind: "entry", trade, text: `${contractLabel(trade)} · ${trade.qty_open ?? trade.qty ?? "—"} חוזים · ${price(trade.entry_price)}` });
    }
    if (trade.closed_at) {
      events.push({ at: trade.closed_at, kind: "exit", trade, text: `${contractLabel(trade)} · ${price(trade.entry_price)} → ${price(trade.exit_price)} · ${exitLabel(trade.exit_reason)}` });
    }
  }
  const blocked = (scan?.stages?.buy_outcomes ?? []).filter((item) => item.result === "blocked");
  if (blocked.length > 0 && scan?.finished_at) {
    for (const item of blocked) {
      events.push({ at: scan.finished_at, kind: "blocked", trade: null, text: `${item.symbol.replace(/\s+/g, " ")} · ${item.reason}` });
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
