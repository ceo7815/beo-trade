"use client";

import Link from "next/link";
import { Shell } from "@/components/Shell";
import { IntegrationCard, Summary, useIntegrations } from "@/components/IntegrationBoard";

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
        <h1>ממשקים פעילים</h1>
        <p className="mt-2 font-bold text-[var(--muted)]">PAPER TRADING. חיבור מוצג רק אחרי בדיקה אמיתית. <Link className="text-[var(--gold)]" href="/integrations/status">סטטוס מערכת</Link></p>
      </div>
      {error ? <p className="font-bold text-[var(--rose)]">{error}</p> : null}
      {data ? <Summary data={data} /> : <p className="font-bold text-[var(--muted)]">טוען...</p>}
      <div className="int-grid">
        {ORDER.flatMap((category) => active.filter((item) => item.category === category)).map((item) => (
          <IntegrationCard key={item.id} item={item} />
        ))}
      </div>
    </div>
  );
}
