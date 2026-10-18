"use client";

import { useEffect, useState } from "react";
import { Shell } from "@/components/Shell";
import { apiGet } from "@/lib/api";

type Release = { version: string; date: string; title: string; changes: string[] };
type Body = { current: string; releases: Release[] };

export default function VersionsPage() {
  const [body, setBody] = useState<Body | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let alive = true;
    apiGet<Body>("/api/v1/release")
      .then((next) => {
        if (!alive) return;
        setBody(next);
        setFailed(false);
      })
      .catch(() => alive && setFailed(true));
    return () => {
      alive = false;
    };
  }, []);

  return (
    <Shell>
      <section className="engine">
        <header className="ops-head">
          <div>
            <h1>גרסאות</h1>
            <p>כל דחיפה לגיטהאב היא גרסה. האתר מציג אותה אחרי Deploy.</p>
          </div>
          <span className={`ops-pip ${body?.current ? "on" : "warn"}`}>
            <i />
            {body?.current ? `גרסה ${body.current}` : failed ? "אין נתונים" : "טוען"}
          </span>
        </header>
        <p className="ops-line">הגרסה שרשומה כאן היא הגרסה שרצה בשרת עכשיו. דחיפה לבדה לא מחליפה את האתר.</p>
        {failed && !body ? <p className="ops-line">השרת לא החזיר את יומן הגרסאות.</p> : null}
        {(body?.releases ?? []).map((release) => (
          <article className="ops-pane" key={release.version}>
            <header className="ops-pane-head">
              <h2>
                {release.version}
                {release.version === body?.current ? " · רצה עכשיו" : ""}
              </h2>
              <span>{release.date}</span>
            </header>
            <p className="ops-line">{release.title}</p>
            <ul className="version-changes">
              {release.changes.map((change) => (
                <li key={change}>{change}</li>
              ))}
            </ul>
          </article>
        ))}
      </section>
    </Shell>
  );
}
