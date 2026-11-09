"use client";

import { useEffect, useState } from "react";
import { Shell } from "@/components/Shell";
import { apiGet } from "@/lib/api";

type SystemsBody = { systems?: { id: string; label?: string; ready?: boolean; status_path?: string | null; note?: string }[] };

export default function AviPage() {
  const [statusPath, setStatusPath] = useState<string | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    let alive = true;
    apiGet<SystemsBody>("/api/v1/systems")
      .then((body) => {
        if (!alive) return;
        const row = (body.systems || []).find((item) => item.id === "avi");
        setReady(Boolean(row?.ready && row.status_path));
        setStatusPath(row?.status_path || null);
      })
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, []);

  return (
    <Shell>
      <section className="avi-board">
        <header className="archive-head">
          <div>
            <p className="spy-kicker">PAPER · מערכת שלישית</p>
            <h1>מסחר יומי אבי</h1>
          </div>
          <span className="archive-badge wait">בפיתוח</span>
        </header>
        <p className="archive-note">
          המנוע עדיין לא מחובר. כשיהיה שירות משלו, הוא נכנס לכאן עם כתובת סטטוס משלו — בלי לגעת באופציות ובלי לגעת במסחר של אלי.
        </p>
        {ready && statusPath ? <p className="archive-note">המנוע זמין ב-{statusPath}</p> : null}
        <div className="avi-grid">
          {["סטטוס", "חשבון", "רווח היום", "פוזיציות", "עסקאות", "היסטוריה", "בלמים"].map((label) => (
            <article key={label}>
              <span>{label}</span>
              <b>—</b>
            </article>
          ))}
        </div>
        <figure className="beo-chart-card avi-soon">
          <figcaption>
            <span>עקומת הון</span>
            <b>תופיע עם המנוע</b>
          </figcaption>
          <svg className="beo-chart" viewBox="0 0 720 140" aria-hidden="true">
            <path className="line still" d="M0 100 C80 96 120 90 180 92 C260 96 300 40 380 46 C460 52 520 70 600 48 C660 34 690 30 720 22" />
          </svg>
        </figure>
      </section>
    </Shell>
  );
}
