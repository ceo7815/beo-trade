"use client";

import { useEffect, useState } from "react";
import { Shell } from "@/components/Shell";
import { apiGet, apiPost } from "@/lib/api";

type Account = Record<string, string | number | boolean | null>;
type Order = Record<string, unknown>;
type Flags = Record<string, boolean>;

const FLAG_LABELS: Record<string, string> = {
  CREDENTIALS_VALID: "CREDENTIALS_VALID",
  API_REACHABLE: "API_REACHABLE",
  ACCOUNT_READABLE: "ACCOUNT_READABLE",
  ORDERS_READABLE: "ORDERS_READABLE",
  POSITIONS_READABLE: "POSITIONS_READABLE",
  STREAM_CONNECTED: "STREAM_CONNECTED",
  RECONCILIATION_OK: "RECONCILIATION_OK",
  PAPER_ONLY: "PAPER_ONLY",
};

function text(value: unknown) {
  if (value === undefined || value === null || value === "") return "אין נתונים";
  return String(value);
}

export default function AlpacaPage() {
  return (
    <Shell>
      <AlpacaBody />
    </Shell>
  );
}

function AlpacaBody() {
  const [account, setAccount] = useState<Account | null>(null);
  const [orders, setOrders] = useState<Order[] | null>(null);
  const [flags, setFlags] = useState<Flags | null>(null);
  const [message, setMessage] = useState("טוען חשבון Alpaca Paper");
  const [missing, setMissing] = useState(false);

  async function refresh() {
    try {
      const [accountBody, orderBody] = await Promise.all([
        apiGet<{ account: Account }>("/api/v1/broker/alpaca/account"),
        apiGet<{ items: Order[] }>("/api/v1/broker/alpaca/orders?status=all"),
      ]);
      setAccount(accountBody.account);
      setOrders(orderBody.items);
      setMissing(false);
      setMessage("החשבון נקרא מ-Alpaca Paper");
    } catch (error) {
      setAccount(null);
      setOrders(null);
      setMissing(true);
      setMessage(error instanceof Error ? error.message : "אין נתוני חשבון");
    }
  }

  useEffect(() => {
    refresh().catch(() => setMissing(true));
    apiGet<{ flags: Flags }>("/api/v1/broker/alpaca/health")
      .then((body) => setFlags(body.flags))
      .catch(() => setFlags(null));
  }, []);

  async function test() {
    try {
      const result = await apiPost<{ status_label: string; detail: string }>("/api/v1/integrations/alpaca/test");
      setMessage(`${result.status_label}. ${result.detail}`);
      if (result.status_label === "מחובר") await refresh();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "הבדיקה נכשלה");
    }
  }

  const accountRows = ["id", "status", "currency", "cash", "equity", "portfolio_value", "buying_power", "regt_buying_power", "long_market_value", "trading_blocked", "options_trading_level", "options_approved_level"];

  return (
    <section className="grid gap-4">
      <div className="panel">
        <h1>מרכז פקודות</h1>
        <p className="mt-2 font-bold text-[var(--gold)]">Alpaca Paper · אין מסחר אמיתי</p>
        <p className="mt-2 font-bold">{missing ? "אין נתוני חשבון" : message}</p>
        <div className="mt-4 flex gap-2">
          <button className="nav-link" onClick={test}>בדוק חיבור</button>
          <button className="nav-link" onClick={() => refresh().catch(() => setMissing(true))}>רענן</button>
        </div>
      </div>
      <div className="panel">
        <h2>חשבון · Alpaca Paper</h2>
        <dl className="mt-4">
          {accountRows.map((key) => (
            <div className="ready" key={key}>
              <span>{key}</span>
              <b className="num">{missing || !account || account[key] === undefined || account[key] === null ? "אין נתוני חשבון" : String(account[key])}</b>
            </div>
          ))}
        </dl>
      </div>
      <div className="panel">
        <h2>בריאות הברוקר</h2>
        {flags ? (
          <dl className="mt-4">
            {Object.keys(FLAG_LABELS).map((key) => (
              <div className="ready" key={key}>
                <span>{key}</span>
                <b>{flags[key] ? "PASS" : "FAIL"}</b>
              </div>
            ))}
          </dl>
        ) : (
          <p className="mt-3 font-bold text-[var(--muted)]">אין נתונים</p>
        )}
      </div>
      <div className="panel overflow-x-auto">
        <h2>פקודות · Alpaca Paper</h2>
        {orders && orders.length === 0 ? <p className="mt-3 font-bold text-[var(--muted)]">אין פקודות.</p> : null}
        {orders && orders.length > 0 ? (
          <table className="blotter">
            <thead>
              <tr>
                <th>Order ID</th>
                <th>Trade ID</th>
                <th>Contract</th>
                <th>Side</th>
                <th>Position Intent</th>
                <th>Qty</th>
                <th>Limit</th>
                <th>Status</th>
                <th>Filled Qty</th>
                <th>Average Fill</th>
                <th>Submitted</th>
                <th>Last Update</th>
                <th>Reject/Error</th>
              </tr>
            </thead>
            <tbody>
              {orders.map((order) => (
                <tr key={text(order.broker_order_id)}>
                  <td className="num">{text(order.broker_order_id)}</td>
                  <td className="num">{text(order.trade_id)}</td>
                  <td className="num">{text(order.symbol)}</td>
                  <td>{text(order.side)}</td>
                  <td>{text(order.position_intent)}</td>
                  <td className="num">{text(order.qty)}</td>
                  <td className="num">{text(order.limit_price)}</td>
                  <td>{text(order.internal_state)}</td>
                  <td className="num">{text(order.filled_qty)}</td>
                  <td className="num">{text(order.filled_avg_price)}</td>
                  <td className="num">{text(order.submitted_at)}</td>
                  <td className="num">{text(order.updated_at)}</td>
                  <td>{text(order.failed_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : null}
      </div>
    </section>
  );
}
