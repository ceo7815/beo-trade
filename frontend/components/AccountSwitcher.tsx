"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { apiGet } from "@/lib/api";
import { readStoredPeriod, writeStoredPeriod, type TradingPeriodRow } from "@/lib/account";

type PeriodsBody = { current_id?: string; periods?: TradingPeriodRow[] };

export function AccountSwitcher() {
  const [period, setPeriod] = useState("current");
  const [rows, setRows] = useState<TradingPeriodRow[]>([]);
  const [currentId, setCurrentId] = useState("paper_20k_ils");

  useEffect(() => {
    setPeriod(readStoredPeriod());
    let alive = true;
    apiGet<PeriodsBody>("/api/v1/trading-periods")
      .then((body) => {
        if (!alive) return;
        setRows(body.periods || []);
        setCurrentId(body.current_id || "paper_20k_ils");
      })
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, []);

  function select(id: string) {
    const next = id === currentId ? "current" : id;
    setPeriod(next);
    writeStoredPeriod(next);
    // Reload so every view (including live caches) picks the new account.
    window.location.reload();
  }

  const activeId = period === "current" ? currentId : period;
  const active = rows.find((row) => row.id === activeId);
  const readOnly = Boolean(active?.read_only);

  return (
    <div className="account-switch" role="group" aria-label="חשבון מסחר">
      <div className="account-switch-row">
        {(rows.length ? rows : [
          { id: "paper_100k", label: "פייפר 100K", read_only: true },
          { id: "paper_10k_ils", label: "פייפר 10K ₪", trading: false },
          { id: "paper_20k_ils", label: "פייפר 20K ₪", trading: true },
        ]).map((row) => {
          const on = row.id === activeId || (period === "current" && row.id === currentId);
          return (
            <button
              key={row.id}
              type="button"
              className={`choice${on ? " on" : ""}`}
              aria-pressed={on}
              onClick={() => select(row.id)}
              title={row.read_only ? "תצוגה בלבד — אין שליחת פקודות" : "חשבון המסחר הפעיל"}
            >
              {row.label}
              {row.read_only ? <span className="muted"> · קריאה</span> : null}
            </button>
          );
        })}
      </div>
      <Link href="/settings/rules" className="rules-link" title="ספר החוקים">
        חוקי מסחר
      </Link>
      {readOnly ? <span className="account-note">תצוגת 100K — בלי פקודות</span> : null}
    </div>
  );
}
