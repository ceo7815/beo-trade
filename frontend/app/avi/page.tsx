"use client";

import { useEffect, useState } from "react";
import { Shell } from "@/components/Shell";
import { SparkChart } from "@/components/charts";
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
          <SparkChart points={[8, 9, 8, 11, 10, 13, 12, 15]} label="הגרף יופיע עם המנוע" />
        </figure>
      </section>
    </Shell>
  );
}
