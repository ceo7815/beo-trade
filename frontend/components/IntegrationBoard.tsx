"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { useDesk } from "@/components/Shell";
import { apiGet } from "@/lib/api";
import { money } from "@/lib/pnl";

export type IntegrationItem = {
  id: string;
  name: string;
  category: string;
  role: string;
  group: string;
  primary: boolean;
  environment: string;
  status: string;
  status_label: string;
  detail: string;
  latency_ms: number | null;
  last_check: string | null;
  key: { status: string; masked: string };
  future_reason: string;
};

export type IntegrationPayload = {
  items: IntegrationItem[];
  summary: { connected: number; warnings: number; errors: number; last_sync: string | null };
  trading_mode: string;
};

export function useIntegrations() {
  const [data, setData] = useState<IntegrationPayload | null>(null);
  const [error, setError] = useState("");

  function load() {
    return apiGet<IntegrationPayload>("/api/v1/integrations")
      .then(setData)
      .catch(() => setError("אין נתונים מהשרת"));
  }

  useEffect(() => {
    load().catch(() => undefined);
  }, []);

  return { data, error, load };
}

export function Summary({ data }: { data: IntegrationPayload }) {
  return (
    <section className="metrics">
      <article className="metric"><span>מחוברים</span><b>{data.summary.connected}</b></article>
      <article className="metric"><span>אזהרות</span><b>{data.summary.warnings}</b></article>
      <article className="metric"><span>תקלות</span><b>{data.summary.errors}</b></article>
      <article className="metric"><span>סנכרון אחרון</span><b className="text-lg">{data.summary.last_sync ? data.summary.last_sync.slice(11, 19) : "אין נתונים"}</b></article>
    </section>
  );
}

const CAPABILITY: Record<string, string> = {
  openai: "מנסח את ההחלטה אחרי שהנתונים עברו. לא שולח פקודות.",
  thetadata: "ציטוטי מניות ואופציות. בלי זה אין גרף ואין BUY.",
  benzinga: "חדשות שמסבירות למה הנכס זז.",
  alpaca: "ביצוע Paper בלבד. לא מקור המחירים.",
  supabase: "שומר היסטוריה, הרשאות ואודיט.",
  redis: "מטמון ותור מקומי. מחובר רק אחרי PING.",
  xcloud: "השרת הקיים. לא מקימים שרת חדש.",
  sec: "דיווחי חברות רשמיים כשיש סתירה בנתונים.",
  fred: "ריבית וסדרות מאקרו.",
  quant: "מחשב יווניות, IV ונפח בקוד.",
  candidate: "מסנן פלח מהיקום הדינמי. על המסך עולה רק מה שעבר.",
  risk: "בודק גודל פוזיציה, מרווח ונזילות לפני אישור.",
  regime: "ממומש — ממתין לנתוני שוק",
  audit: "שומר מה היה ידוע, מה הוחלט ומה קרה אחר כך.",
  backtest: "משחזר עסקאות היסטוריות בלי נתוני עתיד.",
};

export function IntegrationCard({ item }: { item: IntegrationItem }) {
  const { status } = useDesk();
  const spent = Number(status?.ai_month_usd);
  const cap = Number(status?.ai_hard_limit);
  const billing = item.id === "openai"
    ? (status && Number.isFinite(spent) && Number.isFinite(cap) ? `הוצאה החודש ${money(spent)} · תקרה ${money(cap)}` : "אין נתונים")
    : item.group === "future"
      ? "לא פעיל. אין חיוב."
      : item.environment === "INTERNAL"
        ? "מנוע פנימי. אין ספק בתשלום."
        : item.id === "sec"
          ? "מקור ציבורי. אין מפתח ואין חיוב."
          : "אין נתון חיוב במערכת";

  return (
    <article className="int-card">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2>{item.name}</h2>
          <p className="text-xs font-bold text-[var(--gold)]">{item.category}{item.primary ? " · Primary" : ""}</p>
        </div>
        <span className={`health ${tone(item.status)}`}><i />{item.status_label}</span>
      </div>
      <dl className="mt-3">
        <div className="ready"><span>יכולת</span><b>{CAPABILITY[item.id] || item.role}</b></div>
        <div className="ready"><span>חיבור</span><b>{item.status_label}</b></div>
        <div className="ready"><span>חיוב</span><b className="num">{billing}</b></div>
        <div className="ready"><span>סנכרון</span><b className="num">{item.last_check ? item.last_check.slice(11, 19) : "אין נתונים"}</b></div>
        <div className="ready"><span>השהיה</span><b className="num">{item.latency_ms === null ? "אין נתונים" : `${item.latency_ms} ms`}</b></div>
      </dl>
      {item.detail ? <p className="mt-3 text-xs font-bold text-[var(--muted)]">{item.detail}</p> : null}
      <div className="mt-3 flex gap-2">
        <Link className="nav-link" href={`/integrations/${item.id}`}>הגדרות</Link>
        {item.id === "alpaca" ? <Link className="nav-link" href="/broker/alpaca">חשבון Paper</Link> : null}
      </div>
    </article>
  );
}

function tone(status: string) {
  if (status === "connected" || status === "internal") return "ok";
  if (status === "error") return "bad";
  if (status === "planned" || status === "not_built" || status === "disconnected" || status === "missing_key") return "wait";
  return "warn";
}
