"use client";

import { useEffect, useState } from "react";
import { Shell } from "@/components/Shell";
import { copy } from "@/lib/copy";
import { PHASES, apiGet, poll, type SystemStatus } from "@/lib/api";
import { money } from "@/lib/format";
import { regimeLine } from "@/lib/regime";

type Desk = {
  last_scan: {
    recommendations?: number;
    buys?: number;
    ai_calls?: number;
    universe?: { discovered?: number; filtered?: number } | null;
  } | null;
};

type Explain = { title: string; does: string; tools: string[]; formula: string };

export default function ScannerPage() {
  const [desk, setDesk] = useState<Desk | null>(null);
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [open, setOpen] = useState<Explain | null>(null);
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    let alive = true;
    const load = () => {
      return Promise.allSettled([
        apiGet<Desk>("/api/v1/desk"),
        apiGet<SystemStatus>("/api/v1/system"),
      ]).then(([deskResult, statusResult]) => {
        if (!alive) return;
        setDesk(deskResult.status === "fulfilled" ? deskResult.value : null);
        setStatus(statusResult.status === "fulfilled" ? statusResult.value : null);
      });
    };
    const stop = poll(() => load(), 30000);
    const clock = window.setInterval(() => setNow(Date.now()), 1000);
    return () => {
      alive = false;
      stop();
      window.clearInterval(clock);
    };
  }, []);

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  const scan = desk?.last_scan;
  const count = (value: number | null | undefined) => (value == null ? "אין נתונים" : String(value));
  const cubes: { label: string; value: string; explain: Explain }[] = [
    {
      label: "סוננו",
      value: count(scan?.universe?.filtered),
      explain: { title: "סוננו", does: "אותו מונה יקום שמופיע במערכת האוטונומית. בלי סריקה שמורה אין מספר.", tools: ["Alpaca Assets", "last_scan"], formula: "סוננו = universe.filtered\nפלח = 40" },
    },
    {
      label: "התגלו",
      value: count(scan?.universe?.discovered),
      explain: { title: "התגלו", does: "מניות עם אופציות שנמצאו לפני לקיחת הפלח.", tools: ["Alpaca Assets"], formula: "התגלו = universe.discovered" },
    },
    {
      label: "החלטות",
      value: count(scan?.recommendations),
      explain: { title: "החלטות בסריקה", does: "החלטות שנוצרו בסריקה האחרונה. אותו שדה כמו במערכת האוטונומית.", tools: ["last_scan"], formula: "החלטות = last_scan.recommendations" },
    },
    {
      label: "ביצועים",
      value: count(scan?.buys),
      explain: { title: "ביצועים בסריקה", does: "החלטות BUY בסריקה האחרונה. BUY שמור אינו מילוי אצל הברוקר.", tools: ["last_scan"], formula: "ביצועים = count(decision = BUY) בסריקה" },
    },
    {
      label: "קריאות AI",
      value: count(scan?.ai_calls),
      explain: { title: "קריאות AI בסריקה", does: "קריאות המודל בסריקה האחרונה, לא סך החודש.", tools: ["OpenAI Responses"], formula: "קריאות = last_scan.ai_calls\nתקרה = 8 לסריקה" },
    },
    {
      label: "BUY שמורות",
      value: status ? String(status.active_buys) : "אין נתונים",
      explain: { title: "החלטות BUY שמורות", does: "אותו מספר שמופיע בלוח הראשי ובהיסטוריית ההחלטות.", tools: ["RecommendationRow"], formula: "BUY = count(decision = BUY)" },
    },
    {
      label: "שלב שוק",
      value: status ? PHASES[status.market_phase] || status.market_phase : "אין נתונים",
      explain: { title: "שלב שוק", does: "השלב נקבע לפי שעון הבורסה. סריקה לא רצה כשהשוק סגור.", tools: ["שעון מסחר"], formula: "שלב = phase_at(עכשיו, לוח המסחר)" },
    },
    {
      label: "משטר",
      value: regimeLine(status?.regime_status, now),
      explain: {
        title: "משטר שוק",
        does: "הסורק שומר את המשטר האחרון, והמסך קורא את העותק הזה. בלי ציטוט SPY, QQQ, IWM או VIX המשטר נשאר ממתין.",
        tools: ["SPY", "QQQ", "IWM", "VIX"],
        formula: "משטר = מגמה / תנודתיות / סיכון\nעודכן לפני = עכשיו − updated_at",
      },
    },
    {
      label: "הון Alpaca",
      value: equityText(status?.paper_equity),
      explain: {
        title: "הון Alpaca",
        does: "ההון שכבר נקרא מחשבון Alpaca Paper. המסך לא מציג את יתרת הפתיחה הפנימית.",
        tools: ["Alpaca equity"],
        formula: "הון = equity מהקריאה האחרונה לחשבון\nבלי קריאה: אין נתונים",
      },
    },
    {
      label: "דופק",
      value: status?.worker_heartbeat_age_seconds == null ? "אין נתונים" : `${Math.round(status.worker_heartbeat_age_seconds)} שנ׳`,
      explain: { title: "דופק הסורק", does: "גיל קובץ הדופק. המנוע נחשב פעיל רק כשהגיל בתוך חלון הסריקה.", tools: ["worker_heartbeat"], formula: "פעיל אם גיל ≤ מרווח סריקה × 3" },
    },
  ];

  return (
    <Shell>
      <Board title={copy.screens.scanner} hint={copy.screens.engineHint} pip={scan ? "יש סריקה" : desk ? copy.common.waiting : copy.common.loading} cubes={cubes} open={open} setOpen={setOpen} />
    </Shell>
  );
}

function equityText(value: string | null | undefined) {
  if (value == null || value === "") return "אין נתונים";
  const number = Number(value);
  return Number.isFinite(number) ? money(number) : "אין נתונים";
}

function Board({ title, hint, pip, cubes, open, setOpen }: {
  title: string;
  hint: string;
  pip: string;
  cubes: { label: string; value: string; explain: Explain }[];
  open: Explain | null;
  setOpen: (item: Explain | null) => void;
}) {
  return (
    <section className="engine">
      <header className="ops-head">
        <div>
          <h1>{title}</h1>
          <p>{hint}</p>
        </div>
        <span className={`ops-pip ${pip === "טוען" || pip === "ממתין" ? "warn" : "on"}`}><i />{pip}</span>
      </header>
      <div className="plain-list">
        {cubes.map((item) => (
          <button key={item.label} type="button" className="plain-row" onClick={() => setOpen(item.explain)}>
            <b>{item.label}</b>
            <strong className="num">{item.value}</strong>
          </button>
        ))}
      </div>
      {open ? <Dialog item={open} onClose={() => setOpen(null)} kicker={hint} /> : null}
    </section>
  );
}

function Dialog({ item, onClose, kicker }: { item: Explain; onClose: () => void; kicker: string }) {
  return (
    <div className="engine-backdrop" onClick={onClose}>
      <article className="engine-modal" role="dialog" aria-modal="true" onClick={(event) => event.stopPropagation()}>
        <header>
          <div>
            <p>{kicker}</p>
            <h2>{item.title}</h2>
          </div>
          <button type="button" onClick={onClose}>סגור</button>
        </header>
        <h3>מה הנתון</h3>
        <p>{item.does}</p>
        <h3>כלים</h3>
        <ul>{item.tools.map((tool) => <li key={tool}>{tool}</li>)}</ul>
        <h3>נוסחה</h3>
        <pre>{item.formula}</pre>
      </article>
    </div>
  );
}
