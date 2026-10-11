"use client";

import { useEffect, useState } from "react";
import { Shell } from "@/components/Shell";
import { apiGet, poll } from "@/lib/api";
import { exitLabel } from "@/lib/exits";

type Closed = {
  underlying: string;
  right: string;
  opened_at: string;
  closed_at: string;
  dte_at_entry: number | null;
  exit_reason: string;
  pnl: string | null;
};

type Group = { name: string; trades: number; wins: number; pnl: number };

const ZONE = "America/New_York";
const WEEKDAYS = ["ראשון", "שני", "שלישי", "רביעי", "חמישי", "שישי", "שבת"];

function nyParts(stamp: string) {
  const parts = new Intl.DateTimeFormat("en-US", { timeZone: ZONE, hour: "2-digit", minute: "2-digit", hour12: false, weekday: "short", year: "numeric", month: "2-digit", day: "2-digit" }).formatToParts(new Date(stamp));
  const get = (type: string) => parts.find((part) => part.type === type)?.value ?? "";
  const hour = Number(get("hour")) % 24;
  return { minutes: hour * 60 + Number(get("minute")), day: `${get("year")}-${get("month")}-${get("day")}`, weekday: ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"].indexOf(get("weekday")) };
}

function dteGroup(dte: number | null) {
  if (dte == null) return "לא ידוע";
  if (dte <= 0) return "0 ימים";
  if (dte <= 2) return "1–2 ימים";
  if (dte <= 5) return "3–5 ימים";
  return "6 ימים ומעלה";
}

function hourGroup(minutes: number) {
  if (minutes < 11 * 60) return "עד 11:00";
  if (minutes < 13 * 60) return "11:00–13:00";
  if (minutes < 15 * 60) return "13:00–15:00";
  return "אחרי 15:00";
}

function groupBy(rows: Closed[], key: (row: Closed) => string): Group[] {
  const book = new Map<string, Group>();
  for (const row of rows) {
    const value = Number(row.pnl);
    if (!Number.isFinite(value)) continue;
    const name = key(row);
    const group = book.get(name) ?? { name, trades: 0, wins: 0, pnl: 0 };
    group.trades += 1;
    group.wins += value > 0 ? 1 : 0;
    group.pnl += value;
    book.set(name, group);
  }
  return [...book.values()].sort((a, b) => b.pnl - a.pnl);
}

function money(value: number) {
  const sign = value > 0 ? "+" : value < 0 ? "−" : "";
  return `${sign}$${Math.abs(Math.round(value)).toLocaleString("en-US")}`;
}

const VIEWS: { title: string; note: string; key: (row: Closed) => string }[] = [
  { title: "ימים עד פקיעה", note: "כמה ימים נשארו לאופציה ביום הכניסה", key: (row) => dteGroup(row.dte_at_entry) },
  { title: "שעת כניסה", note: "שעון ניו יורק", key: (row) => hourGroup(nyParts(row.opened_at).minutes) },
  { title: "יום בשבוע", note: "יום הכניסה, שעון ניו יורק", key: (row) => WEEKDAYS[nyParts(row.opened_at).weekday] ?? "לא ידוע" },
  { title: "החזקה", note: "נסגרה באותו יום או הוחזקה לילה", key: (row) => (nyParts(row.opened_at).day === nyParts(row.closed_at).day ? "באותו יום" : "הוחזקה לילה") },
  { title: "סיבת יציאה", note: "מה סגר את העסקה", key: (row) => exitLabel(row.exit_reason) },
  { title: "כיוון", note: "CALL או PUT", key: (row) => row.right || "לא ידוע" },
  { title: "מניה", note: "לפי הנכס הבסיסי", key: (row) => row.underlying || "לא ידוע" },
];

export default function LearningPage() {
  const [period, setPeriod] = useState<"all" | "month">("all");
  const [rows, setRows] = useState<Closed[] | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let alive = true;
    setRows(null);
    const stop = poll(async () => {
      try {
        const body = await apiGet<{ closed?: Closed[] }>(`/api/v1/report/trades?period=${period}`, 30000);
        if (!alive) return;
        setRows(body.closed ?? []);
        setFailed(false);
      } catch {
        if (alive) setFailed(true);
      }
    }, 60000);
    return () => {
      alive = false;
      stop();
    };
  }, [period]);

  const total = rows ? groupBy(rows, () => "הכל")[0] : undefined;

  return (
    <Shell>
      <section className="engine">
        <header className="ops-head">
          <div>
            <h1>מה עובד</h1>
            <p>עסקאות סגורות לפי קבוצות · PAPER</p>
          </div>
          <div className="ops-pips">
            <button type="button" className={`ops-pip ${period === "all" ? "on" : ""}`} onClick={() => setPeriod("all")}>הכל</button>
            <button type="button" className={`ops-pip ${period === "month" ? "on" : ""}`} onClick={() => setPeriod("month")}>החודש</button>
          </div>
        </header>
        <p className="ops-line">
          {failed && !rows
            ? "אין נתונים. השרת לא החזיר את דוח העסקאות."
            : !rows
              ? "טוען"
              : total
                ? `${total.trades} עסקאות סגורות · ${Math.round((total.wins / total.trades) * 100)}% מרוויחות · ${money(total.pnl)}. מעט עסקאות בקבוצה הן כיוון, לא הוכחה.`
                : "אין עדיין עסקאות סגורות בתקופה הזו"}
        </p>
        {rows && rows.length
          ? VIEWS.map((view) => (
              <section className="ops-pane" key={view.title}>
                <header className="ops-pane-head">
                  <h2>{view.title}</h2>
                  <span>{view.note}</span>
                </header>
                <table className="ops-table ops-cardify">
                  <thead>
                    <tr><th>קבוצה</th><th>עסקאות</th><th>מרוויחות</th><th>רווח/הפסד</th><th>ממוצע לעסקה</th></tr>
                  </thead>
                  <tbody>
                    {groupBy(rows, view.key).map((group) => (
                      <tr key={group.name}>
                        <td><bdi>{group.name}</bdi></td>
                        <td className="num">{group.trades}</td>
                        <td className="num">{Math.round((group.wins / group.trades) * 100)}%</td>
                        <td className={`num ${group.pnl > 0 ? "up" : group.pnl < 0 ? "down" : ""}`}>{money(group.pnl)}</td>
                        <td className="num">{money(group.pnl / group.trades)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </section>
            ))
          : null}
      </section>
    </Shell>
  );
}
