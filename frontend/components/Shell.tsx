"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { createContext, useContext, useEffect, useMemo, useState } from "react";
import { PHASES, apiGet, apiPost, type SystemStatus } from "@/lib/api";
import { LogoLockup } from "@/components/Logo";

type DeskData = { query: string; status: SystemStatus | null };
const DeskContext = createContext<DeskData>({ query: "", status: null });
export function useDesk() {
  return useContext(DeskContext);
}
export function useDeskQuery() {
  return useContext(DeskContext).query;
}

function NavIcon({ d }: { d: string }) {
  return (
    <svg viewBox="0 0 24 24" width="15" height="15" aria-hidden="true">
      <path d={d} fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

const icons = {
  desk: "M4 4h7v7H4zM13 4h7v7h-7zM4 13h7v7H4zM13 13h7v7h-7z",
  scan: "M12 5a7 7 0 1 0 7 7M12 12l4-2M12 3v2M12 19v2M3 12h2M19 12h2",
  buy: "M12 4v16M7 9l5-5 5 5",
  paper: "M7 3h8l4 4v14H7zM15 3v4h4",
  positions: "M4 7h16M4 12h16M4 17h10",
  history: "M12 7v6l4 2M12 21a9 9 0 1 0-9-9",
  performance: "M4 19V10M10 19V5M16 19v-7M22 19H2",
  ai: "M12 3l1.6 5.2L19 10l-5.4 1.8L12 17l-1.6-5.2L5 10l5.4-1.8z",
  status: "M4 12h3l2-5 4 10 2-5h5",
  settings: "M4 7h16M4 12h10M4 17h13",
};

function useClock(timeZone: string) {
  const [value, setValue] = useState({ time: "--:--:--", date: "--.--.----" });
  useEffect(() => {
    const timeFormat = new Intl.DateTimeFormat("en-GB", {
      timeZone,
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
    });
    const dateFormat = new Intl.DateTimeFormat("en-GB", {
      timeZone,
      day: "2-digit",
      month: "2-digit",
      year: "numeric",
    });
    const tick = () => {
      const now = new Date();
      setValue({
        time: timeFormat.format(now),
        date: dateFormat.format(now).replace(/\//g, "."),
      });
    };
    tick();
    const timer = setInterval(tick, 1000);
    return () => clearInterval(timer);
  }, [timeZone]);
  const [hour, minute, second] = value.time.split(":");
  return { hour, minute, second, date: value.date };
}

function Clock({ timeZone, label }: { timeZone: string; label: string }) {
  const clock = useClock(timeZone);
  return (
    <div className="bar-item">
      <div>
        <div className="kicker">{label}</div>
        <div className="bar-date num">{clock.date}</div>
      </div>
      <div className="clock primary">
        <span>{clock.hour}</span>
        <span>:</span>
        <span>{clock.minute}</span>
        <span className="dim">:</span>
        <span className="dim">{clock.second}</span>
      </div>
    </div>
  );
}

export function Shell({ children }: { children: React.ReactNode }) {
  const path = usePathname();
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [note, setNote] = useState("");
  const [link, setLink] = useState<"pending" | "up" | "down">("pending");
  const [halted, setHalted] = useState(false);
  const [switching, setSwitching] = useState(false);
  const [closing, setClosing] = useState(false);

  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const nextStatus = await apiGet<SystemStatus>("/api/v1/system");
        if (!alive) return;
        setStatus(nextStatus);
        setLink("up");
      } catch {
        if (alive) {
          setLink("down");
          setNote("החיבור לשרת נקטע");
        }
      }
    };
    load();
    const timer = setInterval(load, 5000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);

  useEffect(() => {
    if (switching || status?.trading_halted == null) return;
    setHalted(status.trading_halted);
  }, [status, switching]);

  async function toggleTrading() {
    const next = !halted;
    setSwitching(true);
    setHalted(next);
    try {
      const body = await apiPost<{ halted: boolean }>("/api/v1/control/trading", { halted: next });
      setHalted(body.halted);
      setNote("");
    } catch {
      setHalted(!next);
      setNote("המתג לא נשמר. המסחר לא השתנה.");
    } finally {
      setSwitching(false);
    }
  }

  async function closePositions() {
    if (!window.confirm("לשלוח סגירה לכל פוזיציה פתוחה ב-Paper? בלי bid אמיתי הפקודה לא תישלח.")) return;
    setClosing(true);
    try {
      const body = await apiPost<{ submitted: string[]; blocked_positions: { symbol: string }[] }>("/api/v1/control/close-all", {});
      const blocked = body.blocked_positions?.length ?? 0;
      setNote(`נשלחו ${body.submitted?.length ?? 0} פקודות סגירה. ${blocked} נחסמו בלי ציטוט.`);
    } catch {
      setNote("סגירת הפוזיציות לא הושלמה.");
    } finally {
      setClosing(false);
    }
  }

  const progress = useMemo(() => {
    if (!status?.session_open || !status.session_close) return 0;
    const start = new Date(status.session_open).getTime();
    const end = new Date(status.session_close).getTime();
    if (end <= start) return 0;
    return Math.min(100, Math.max(0, ((Date.now() - start) / (end - start)) * 100));
  }, [status]);

  const groups: { title: string; items: { href: string; label: string; icon: string; badge?: number }[] }[] = [
    {
      title: "מערכת",
      items: [
        { href: "/", label: "לוח ראשי", icon: icons.desk },
        { href: "/engine", label: "המערכת האוטונומית", icon: icons.scan },
        { href: "/positions", label: "פוזיציות פתוחות", icon: icons.positions },
        { href: "/history", label: "היסטוריית עסקאות", icon: icons.history },
        { href: "/decisions", label: "היסטוריית החלטות", icon: icons.buy },
      ],
    },
    {
      title: "כספים",
      items: [
        { href: "/finance", label: "בקרת כספים", icon: icons.performance },
        { href: "/risk", label: "בקרת סיכון", icon: icons.performance },
      ],
    },
    {
      title: "מודיעין",
      items: [
        { href: "/scanner", label: "מודיעין שוק", icon: icons.scan },
        { href: "/ai", label: "מרכז החלטות AI", icon: icons.ai },
        { href: "/integrations", label: "ממשקים", icon: icons.settings },
      ],
    },
    {
      title: "תפעול",
      items: [
        { href: "/status", label: "בריאות המערכת", icon: icons.status },
        { href: "/audit", label: "ביקורת מערכת", icon: icons.history },
        { href: "/backtests", label: "בדיקות עבר", icon: icons.scan },
        { href: "/settings", label: "הגדרות", icon: icons.settings },
      ],
    },
  ];

  function active(href: string) {
    if (href === "/") return path === "/";
    if (href === "/opportunities") return path === href || path.startsWith("/opportunity/");
    if (href === "/integrations") return path === "/integrations" || /^\/integrations\/(?!future|status)/.test(path);
    return path === href || path.startsWith(`${href}/`);
  }

  return (
    <DeskContext.Provider value={{ query: "", status }}>
      <div className="desk">
        <aside className="rail">
          <div className="brand-block">
            <LogoLockup />
            <span className={`conn ${link}`}>
              <i />
              {link === "up" ? "מחובר" : link === "down" ? "אין חיבור" : "מתחבר"}
            </span>
            <span className="paper-flag">PAPER TRADING</span>
          </div>
          <nav>
            {groups.map((group) => (
              <div key={group.title} className="nav-group">
                <div className="nav-label">{group.title}</div>
                {group.items.map((item) => (
                  <Link
                    key={item.href}
                    href={item.href}
                    className={`nav-link${active(item.href) ? " active" : ""}`}
                  >
                    <span className="nav-name">
                      <NavIcon d={item.icon} />
                      <span>{item.label}</span>
                    </span>
                    {item.badge ? <span className="badge">{item.badge}</span> : null}
                  </Link>
                ))}
              </div>
            ))}
          </nav>
        </aside>
        <div className="stage">
          <header className="instrument">
            <div className="bar-item">
              <div>
                <div className="kicker">מצב שוק</div>
                <b>{status ? PHASES[status.market_phase] || status.market_phase : "מתחבר"}</b>
              </div>
              <div className="session-track" aria-hidden>
                <div className="session-fill" style={{ width: `${progress}%` }} />
              </div>
            </div>
            <Clock timeZone="America/New_York" label="ניו יורק" />
            <Clock timeZone="Asia/Dubai" label="דובאי" />
            <div className="bar-actions">
              <button
                type="button"
                className={`halt-btn ${halted ? "run" : "stop"}`}
                aria-pressed={halted}
                disabled={switching}
                onClick={toggleTrading}
              >
                {switching ? "מעדכן" : halted ? "הפעל כניסות" : "עצור כניסות"}
              </button>
              <button type="button" className="halt-btn run" disabled={closing} onClick={closePositions}>
                {closing ? "סוגר" : "סגור פוזיציות"}
              </button>
            </div>
          </header>
          {note ? <p className="px-6 pt-3 text-sm text-[var(--rose)]">{note}</p> : null}
          <div className="stage-body">{children}</div>
        </div>
      </div>
    </DeskContext.Provider>
  );
}
