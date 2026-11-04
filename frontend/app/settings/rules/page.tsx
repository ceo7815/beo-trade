"use client";

import { useEffect, useState } from "react";
import { Shell } from "@/components/Shell";
import { apiGet } from "@/lib/api";

type Rule = { id: string; title: string; value: string; explain: string; dollars?: string; ils?: string };
type Tier = {
  id: string;
  label: string;
  per_trade: string;
  kill: string;
  arm: string;
  protect: string;
  exposure: string;
  provisional?: boolean;
  explain: string;
};
type Body = {
  available?: boolean;
  release?: string;
  active?: {
    profile?: string;
    tier?: { name?: string; provisional?: boolean };
    equity_usd?: string;
    equity_ils?: string;
    ils_per_usd?: string;
    fx_source?: string;
    working_capital_usd?: string;
    working_capital_ils?: string;
    sections?: {
      account_sizing?: Rule[];
      entry?: Rule[];
      per_trade_exits?: Rule[];
      day_brakes?: Rule[];
      tiers?: Tier[];
    };
    compare_100k?: Record<string, string>;
    ladder?: { arm: string | null; floor: string | null }[];
    kill_locked?: boolean;
    kill_open_equity?: string | null;
    call_block_dubai?: string;
  };
  trading_periods?: { current_id?: string; periods?: { id: string; label: string; is_current?: boolean }[] };
};

function Section({ title, rules }: { title: string; rules?: Rule[] }) {
  if (!rules?.length) return null;
  return (
    <section className="ops-card rules-section">
      <h2>{title}</h2>
      <ul className="rules-list">
        {rules.map((rule) => (
          <li key={rule.id}>
            <div className="rules-head">
              <b>{rule.title}</b>
              <span className="num">{rule.value}</span>
            </div>
            {(rule.dollars || rule.ils) && (
              <div className="rules-money muted">
                {rule.dollars ? <span>{rule.dollars}$</span> : null}
                {rule.ils ? <span>{rule.ils} ₪</span> : null}
              </div>
            )}
            <p>{rule.explain}</p>
          </li>
        ))}
      </ul>
    </section>
  );
}

export default function TradingRulesPage() {
  const [body, setBody] = useState<Body | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let alive = true;
    apiGet<Body>("/api/v1/trading-rules", 30000)
      .then((next) => alive && setBody(next))
      .catch(() => alive && setError("לא ניתן לטעון את ספר החוקים"));
    return () => {
      alive = false;
    };
  }, []);

  const active = body?.active;
  const sections = active?.sections;

  return (
    <Shell>
      <main className="page rules-page" dir="rtl">
        <header className="page-head">
          <div>
            <p className="kicker">הגדרות · קריאה בלבד</p>
            <h1>חוקי מסחר</h1>
            <p className="muted">
              פרופיל פעיל: <b>{active?.profile || "…"}</b>
              {active?.tier?.name ? <> · שכבה {active.tier.name}{active.tier.provisional ? " (הערכה)" : ""}</> : null}
              {body?.release ? <> · גרסה {body.release}</> : null}
            </p>
          </div>
          <div className="rules-fx ops-card">
            <div className="kicker">שער FX</div>
            <b className="num">{active?.ils_per_usd || "3.14"} ILS / USD</b>
            <p className="muted">מקור: {active?.fx_source || "config"} · נפילה ל־3.14</p>
            <p className="muted">
              הון עבודה: {active?.working_capital_ils || "20000"} ₪ ≈ {active?.working_capital_usd || "6369.43"}$
            </p>
            {active?.equity_usd ? (
              <p className="muted">
                הון חי: {active.equity_usd}$ {active.equity_ils ? `(${active.equity_ils} ₪)` : ""}
              </p>
            ) : null}
            <p className="muted">
              קייל נעול לפתיחה{active?.kill_open_equity ? `: ${active.kill_open_equity}$` : ""}
              {active?.call_block_dubai ? ` · CALL עד ${active.call_block_dubai} דובאי` : ""}
            </p>
          </div>
        </header>

        {error ? <p className="text-[var(--down)]">{error}</p> : null}

        <Section title="חשבון וגודל" rules={sections?.account_sizing} />
        <Section title="כניסה" rules={sections?.entry} />
        <Section title="יציאות לעסקה" rules={sections?.per_trade_exits} />
        <Section title="בלמי יום" rules={sections?.day_brakes} />

        <section className="ops-card rules-section">
          <h2>שכבות גודל לפי הון ב־₪</h2>
          <div className="rules-tiers">
            {(sections?.tiers || []).map((tier) => (
              <article key={tier.id} className="tier-card">
                <h3>
                  {tier.label}
                  {tier.provisional ? <span className="muted"> · הערכה</span> : null}
                </h3>
                <ul>
                  <li>עסקה: {tier.per_trade}</li>
                  <li>קייל: {tier.kill}</li>
                  <li>Arm / Protect: {tier.arm} / {tier.protect}</li>
                  <li>חשיפה: {tier.exposure}</li>
                </ul>
                <p>{tier.explain}</p>
              </article>
            ))}
          </div>
        </section>

        <section className="ops-card rules-section">
          <h2>השוואה לפרופיל 100K</h2>
          <ul className="rules-list">
            <li>
              <div className="rules-head">
                <b>מגבלת עסקה / חשיפה</b>
                <span>
                  {active?.compare_100k?.per_trade} / {active?.compare_100k?.exposure}
                </span>
              </div>
            </li>
            <li>
              <div className="rules-head">
                <b>קייל</b>
                <span>{active?.compare_100k?.kill_usd}$</span>
              </div>
            </li>
            <li>
              <div className="rules-head">
                <b>רצפת יום</b>
                <span>{active?.compare_100k?.day_floor}</span>
              </div>
            </li>
          </ul>
          <p className="muted">המסך לקריאה בלבד — אין עריכה מהממשק.</p>
        </section>
      </main>
    </Shell>
  );
}
