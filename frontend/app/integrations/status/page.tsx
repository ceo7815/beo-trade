"use client";

import { Shell } from "@/components/Shell";
import { useIntegrations } from "@/components/IntegrationBoard";

export default function IntegrationStatusPage() {
  return (
    <Shell>
      <StatusBody />
    </Shell>
  );
}

function StatusBody() {
  const { data, error } = useIntegrations();
  const items = data?.items.filter((item) => item.group === "active") ?? [];
  return (
    <div>
      <h1>סטטוס מערכת</h1>
      <p className="mt-2 font-bold text-[var(--muted)]">מצב אחרון שנבדק. אין כאן ירוק בלי בדיקה.</p>
      {error ? <p className="mt-4 font-bold text-[var(--rose)]">{error}</p> : null}
      <section className="panel mt-6 max-w-3xl">
        {items.map((item) => (
          <div className="ready" key={item.id}>
            <span>{item.name}</span>
            <span className="text-left">
              <b className={item.status === "connected" || item.status === "internal" ? "ok" : "wait"}>{item.status_label}</b>
              <span className="mt-1 block text-xs text-[var(--muted)]">
                {item.last_check ? item.last_check.slice(11, 19) : "אין בדיקה"}
                {item.latency_ms !== null ? ` · ${item.latency_ms} ms` : ""}
                {item.environment ? ` · ${item.environment}` : ""}
              </span>
            </span>
          </div>
        ))}
      </section>
    </div>
  );
}
