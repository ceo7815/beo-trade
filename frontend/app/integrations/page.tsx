"use client";

import Link from "next/link";
import { Shell } from "@/components/Shell";
import { copy } from "@/lib/copy";
import { useIntegrations } from "@/components/IntegrationBoard";

const ORDER = ["AI", "Market Data", "News", "Broker / Paper Execution", "Database", "Infrastructure", "Regulatory", "Macro", "Internal Intelligence"];

export default function IntegrationsPage() {
  return (
    <Shell>
      <Hub />
    </Shell>
  );
}

function Hub() {
  const { data, error } = useIntegrations();
  const active = data?.items.filter((item) => item.group === "active") ?? [];

  return (
    <div className="grid gap-4">
      <div>
        <h1>{copy.screens.integrations}</h1>
        <p className="mt-2 font-bold text-[var(--muted)]">PAPER TRADING. חיבור מוצג רק אחרי בדיקה אמיתית. <Link className="text-[var(--gold)]" href="/integrations/status">סטטוס מערכת</Link></p>
      </div>
      {error ? <p className="font-bold text-[var(--rose)]">{error}</p> : null}
      {data ? <p className="font-bold">{data.summary.connected} מחוברים · {data.summary.warnings} אזהרות · {data.summary.errors} תקלות</p> : <p className="font-bold text-[var(--muted)]">טוען...</p>}
      <div className="plain-list">
        {ORDER.flatMap((category) => active.filter((item) => item.category === category)).map((item) => (
          <Link key={item.id} className="plain-row" href={`/integrations/${item.id}`}>
            <b>{item.name}<small>{item.category}</small></b>
            <strong className={item.status === "connected" || item.status === "internal" ? "ok" : item.status === "error" ? "bad" : "warn"}>{item.status_label}</strong>
          </Link>
        ))}
      </div>
    </div>
  );
}
