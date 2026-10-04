"use client";

import { useParams } from "next/navigation";
import { useState } from "react";
import { Shell } from "@/components/Shell";
import { useIntegrations } from "@/components/IntegrationBoard";
import { apiPost } from "@/lib/api";

const FIELDS: Record<string, { name: string; label: string }[]> = {
  openai: [{ name: "openai_api_key", label: "OpenAI API Key" }],
  alpaca: [
    { name: "alpaca_api_key", label: "Alpaca Paper Key" },
    { name: "alpaca_api_secret", label: "Alpaca Paper Secret" },
  ],
  thetadata: [{ name: "thetadata_api_key", label: "ThetaData API Key" }],
  benzinga: [{ name: "benzinga_api_key", label: "Benzinga API Key" }],
  fred: [{ name: "fred_api_key", label: "FRED API Key" }],
  redis: [{ name: "redis_url", label: "Redis URL" }],
  supabase: [{ name: "database_url", label: "Database URL" }],
};

export default function IntegrationDetailPage() {
  return (
    <Shell>
      <Detail />
    </Shell>
  );
}

function Detail() {
  const params = useParams<{ id: string }>();
  const { data, error, load } = useIntegrations();
  const item = data?.items.find((row) => row.id === params.id);
  const [fields, setFields] = useState<Record<string, string>>({});
  const [message, setMessage] = useState("");
  const inputs = FIELDS[params.id] ?? [];

  async function save() {
    try {
      const result = await apiPost<{ detail: string }>(`/api/v1/integrations/${params.id}/secrets`, { fields });
      setMessage(result.detail);
      setFields({});
      await load();
    } catch (err) {
      setMessage(err instanceof Error ? err.message : "השמירה נכשלה");
    }
  }

  async function test() {
    try {
      const result = await apiPost<{ status_label: string; detail: string }>(`/api/v1/integrations/${params.id}/test`);
      setMessage(`${result.status_label}. ${result.detail}`);
      await load();
    } catch (err) {
      setMessage(err instanceof Error ? err.message : "הבדיקה נכשלה");
    }
  }

  return (
    <section className="panel max-w-2xl">
      <h1>{item?.name || params.id}</h1>
      <p className="mt-2 font-bold text-[var(--muted)]">{item?.role}</p>
      {error ? <p className="mt-3 font-bold text-[var(--rose)]">{error}</p> : null}
      <ol className="mt-4 space-y-1 text-sm font-bold text-[var(--muted)]">
        <li>1. פרטי גישה</li>
        <li>2. בדיקת אימות</li>
        <li>3. קריאת חשבון או מטא-דאטה</li>
        <li>4. בדיקת הרשאה</li>
        <li>5. בדיקת נתון</li>
        <li>6. שמירה בשרת, לא בדפדפן</li>
        <li>7. מחובר רק אם הבדיקה עברה</li>
      </ol>
      <div className="mt-4 grid gap-3">
        {inputs.map((field) => (
          <label key={field.name} className="text-sm font-bold text-[var(--muted)]">
            {field.label}
            <input
              className="field mt-1"
              type="password"
              autoComplete="off"
              value={fields[field.name] || ""}
              placeholder={item?.key.masked || ""}
              onChange={(event) => setFields((current) => ({ ...current, [field.name]: event.target.value }))}
            />
          </label>
        ))}
      </div>
      <div className="mt-4 flex gap-2">
        {inputs.length ? <button className="nav-link" onClick={save}>שמור</button> : null}
        <button className="nav-link" onClick={test}>בדיקת חיבור</button>
      </div>
      {item ? <p className="mt-4 font-bold">{item.status_label}. {item.detail}</p> : null}
      {message ? <p className="mt-2 font-bold text-[var(--gold)]">{message}</p> : null}
    </section>
  );
}
