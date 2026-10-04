"use client";

import { useEffect, useMemo, useState } from "react";
import { Shell } from "@/components/Shell";
import { apiGet } from "@/lib/api";
import { contractResult, money, percent } from "@/lib/pnl";

type AlpacaAccount = { equity?: string; cash?: string; buying_power?: string; portfolio_value?: string };

export default function CalculatorPage() {
  const [account, setAccount] = useState<AlpacaAccount | null>(null);
  const [entry, setEntry] = useState("2.40");
  const [exit, setExit] = useState("3.10");
  const [contracts, setContracts] = useState("1");

  useEffect(() => {
    apiGet<{ account?: AlpacaAccount }>("/api/v1/broker/alpaca/account")
      .then((body) => setAccount(body.account ?? null))
      .catch(() => setAccount(null));
  }, []);

  const scratch = useMemo(
    () => contractResult(Number(entry) || 0, Number(exit) || 0, Number(contracts) || 0),
    [entry, exit, contracts],
  );

  return (
    <Shell>
      <div className="grid gap-4 lg:grid-cols-[1.1fr_0.9fr]">
        <section className="panel">
          <h1 className="text-lg">חשבון Alpaca Paper</h1>
          <p className="mt-1 text-sm text-[var(--muted)]">היתרות נלקחות מחשבון Alpaca Paper.</p>
          <div className="mt-6 grid gap-4 sm:grid-cols-2">
            <Line label="הון עצמי" value={account?.equity ? money(Number(account.equity)) : "אין נתונים"} />
            <Line label="מזומן" value={account?.cash ? money(Number(account.cash)) : "אין נתונים"} />
            <Line label="כוח קנייה" value={account?.buying_power ? money(Number(account.buying_power)) : "אין נתונים"} />
            <Line label="שווי תיק" value={account?.portfolio_value ? money(Number(account.portfolio_value)) : "אין נתונים"} />
          </div>
        </section>
        <section className="panel">
          <h2>מחשבון עסקה</h2>
          <div className="mt-4 grid gap-3">
            <label className="text-sm text-[var(--muted)]">מחיר כניסה
              <input className="field num mt-1" value={entry} onChange={(event) => setEntry(event.target.value)} />
            </label>
            <label className="text-sm text-[var(--muted)]">מחיר יציאה
              <input className="field num mt-1" value={exit} onChange={(event) => setExit(event.target.value)} />
            </label>
            <label className="text-sm text-[var(--muted)]">חוזים
              <input className="field num mt-1" value={contracts} onChange={(event) => setContracts(event.target.value)} />
            </label>
          </div>
          <div className="mt-6">
            <div className="text-sm text-[var(--muted)]">הון שהעסקה דורשת</div>
            <div className="figure">{money(scratch.invested)}</div>
            <div className={`figure ${scratch.profit < 0 ? "down" : "up"}`}>{money(scratch.profit)}</div>
            <div className={scratch.percent < 0 ? "down" : "up"}>{percent(scratch.percent)}</div>
          </div>
        </section>
      </div>
    </Shell>
  );
}

function Line({ label, value, tone }: { label: string; value: string; tone?: "up" | "down" }) {
  return (
    <div>
      <div className="text-sm text-[var(--muted)]">{label}</div>
      <div className={`figure ${tone ?? ""}`}>{value}</div>
    </div>
  );
}
