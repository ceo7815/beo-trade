"use client";

import { Shell } from "@/components/Shell";
import { IntegrationCard, useIntegrations } from "@/components/IntegrationBoard";

export default function FutureIntegrationsPage() {
  return (
    <Shell>
      <FutureBody />
    </Shell>
  );
}

function FutureBody() {
  const { data, error } = useIntegrations();
  const items = data?.items.filter((item) => item.group === "future") ?? [];
  return (
    <div>
      <h1>ממשקים עתידיים</h1>
      <p className="mt-2 font-bold text-[var(--muted)]">לא מופעלים. ThetaData נשארת מקור השוק, ו-Alpaca נשארת ביצוע Paper.</p>
      {error ? <p className="mt-4 font-bold text-[var(--rose)]">{error}</p> : null}
      <div className="int-grid mt-6">
        {items.map((item) => <IntegrationCard key={item.id} item={item} />)}
      </div>
    </div>
  );
}
