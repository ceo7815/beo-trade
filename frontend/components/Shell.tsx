"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { createContext, useContext, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { motion, useReducedMotion } from "framer-motion";
import { PHASES, apiGet, apiPost, poll, warm, type SystemStatus } from "@/lib/api";
import { copy } from "@/lib/copy";
import { num } from "@/lib/format";
import { LogoLockup } from "@/components/Logo";
import { Heartbeat, Ticker, Toasts } from "@/components/live-ui";
import { LiveProvider, useLive } from "@/lib/live";

type Release = { current: string; releases: { version: string; date: string; title: string; changes: string[] }[] };
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

function TabIcon({ d }: { d: string }) {
  return (
    <svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true">
      <path d={d} fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

const WARM: Record<string, string[]> = {
  "/": ["/api/v1/desk", "/api/v1/broker/alpaca/positions", "/api/v1/risk", "/api/v1/report/trades?period=today"],
  "/positions": ["/api/v1/broker/alpaca/positions", "/api/v1/risk", "/api/v1/report/trades?period=today"],
  "/history": ["/api/v1/report/trades?period=all"],
  "/learning": ["/api/v1/learning/trades?period=all"],
  "/finance": ["/api/v1/finance"],
  "/risk": ["/api/v1/risk"],
};

const icons = {
  desk: "M4 4h7v7H4zM13 4h7v7h-7zM4 13h7v7H4zM13 13h7v7h-7z",
  scan: "M12 5a7 7 0 1 0 7 7M12 12l4-2M12 3v2M12 19v2M3 12h2M19 12h2",
  buy: "M12 4v16M7 9l5-5 5 5",
  paper: "M7 3h8l4 4v14H7zM15 3v4h4",
  positions: "M4 7h16M4 12h16M4 17h10",
  history: "M12 7v6l4 2M12 21a9 9 0 1 0-9-9",
  decisions: "M9 6h11M9 12h11M9 18h11M4 6h.01M4 12h.01M4 18h.01",
  performance: "M4 19V10M10 19V5M16 19v-7M22 19H2",
  ai: "M12 3l1.6 5.2L19 10l-5.4 1.8L12 17l-1.6-5.2L5 10l5.4-1.8z",
  status: "M4 12h3l2-5 4 10 2-5h5",
  settings: "M4 7h16M4 12h10M4 17h13",
  version: "M7 4h10v16H7zM9 8h6M9 12h6M9 16h4",
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

function MobileClock() {
  const clock = useClock("America/New_York");
  return <b className="num">{clock.hour}:{clock.minute}</b>;
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

function MobileTabBar({ children }: { children: React.ReactNode }) {
  const dock = useRef<HTMLDivElement>(null);
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);
  useEffect(() => {
    if (!mounted) return;
    const node = dock.current;
    if (!node) return;
    const pin = () => {
      const viewport = window.visualViewport;
      const lift = viewport ? Math.max(0, window.innerHeight - viewport.offsetTop - viewport.height) : 0;
      node.style.setProperty("--tab-lift", `${lift}px`);
    };
    pin();
    const viewport = window.visualViewport;
    viewport?.addEventListener("resize", pin);
    viewport?.addEventListener("scroll", pin);
    window.addEventListener("orientationchange", pin);
    return () => {
      viewport?.removeEventListener("resize", pin);
      viewport?.removeEventListener("scroll", pin);
      window.removeEventListener("orientationchange", pin);
    };
  }, [mounted]);
  if (!mounted) return null;
  return createPortal(
    <div className="tabbar-dock" ref={dock}>
      <nav className="tabbar" aria-label="ניווט ראשי">
        {children}
      </nav>
    </div>,
    document.body,
  );
}

export function Shell({ children }: { children: React.ReactNode }) {
  return (
    <LiveProvider>
      <ShellFrame>{children}</ShellFrame>
    </LiveProvider>
  );
}

function ShellFrame({ children }: { children: React.ReactNode }) {
  const path = usePathname();
  const live = useLive();
  const reduce = useReducedMotion();
  const [query, setQuery] = useState("");
  const [finder, setFinder] = useState(false);
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [note, setNote] = useState("");
  const [link, setLink] = useState<"pending" | "up" | "down">("pending");
  const [halted, setHalted] = useState(false);
  const [switching, setSwitching] = useState(false);
  const [closing, setClosing] = useState(false);
  const [menu, setMenu] = useState(false);
  const [release, setRelease] = useState<Release | null>(null);

  useEffect(() => {
    setMenu(false);
  }, [path]);

  useEffect(() => {
    let alive = true;
    apiGet<Release>("/api/v1/release")
      .then((body) => alive && setRelease(body))
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, []);

  useEffect(() => {
    let alive = true;
    let misses = 0;
    const stop = poll(async () => {
      try {
        const nextStatus = await apiGet<SystemStatus>("/api/v1/system");
        if (!alive) return;
        misses = 0;
        setStatus(nextStatus);
        setLink("up");
        setNote((current) => (current === "החיבור לשרת נקטע" ? "" : current));
      } catch {
        misses += 1;
        if (alive && misses >= 3) {
          setLink("down");
          setNote("החיבור לשרת נקטע");
        }
      }
    }, 15000);
    return () => {
      alive = false;
      stop();
    };
  }, []);

  useEffect(() => {
    if (switching || status?.trading_halted == null) return;
    setHalted(status.trading_halted);
  }, [status, switching]);

  const shown = useMemo(() => {
    if (!status) return status;
    const liveStatus = live.snap?.status;
    if (!liveStatus) return status;
    return {
      ...status,
      market_phase: liveStatus.market_phase || status.market_phase,
      session_open: liveStatus.session_open ?? status.session_open,
      session_close: liveStatus.session_close ?? status.session_close,
      trading_halted: liveStatus.trading_halted ?? status.trading_halted,
    };
  }, [status, live.snap]);

  useEffect(() => {
    const pnl = num(live.snap?.day?.mark_pnl ?? live.snap?.day?.pnl);
    document.body.classList.toggle("red", pnl !== null && pnl < 0);
    document.body.style.setProperty("--mood", pnl === null ? "0.5" : String(Math.max(-1, Math.min(1, pnl / 2500))));
  }, [live.snap]);

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
        { href: "/learning", label: "מה עובד", icon: icons.performance },
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
        { href: "/settings", label: "הגדרות", icon: icons.settings },
        { href: "/versions", label: "גרסאות", icon: icons.version },
      ],
    },
  ];

  function active(href: string) {
    if (href === "/") return path === "/";
    if (href === "/opportunities") return path === href || path.startsWith("/opportunity/");
    if (href === "/integrations") return path === "/integrations" || /^\/integrations\/(?!future|status)/.test(path);
    return path === href || path.startsWith(`${href}/`);
  }

  const tabs = [
    { href: "/", label: "לוח", icon: icons.desk },
    { href: "/positions", label: "פוזיציות", icon: icons.positions },
    { href: "/history", label: "עסקאות", icon: icons.history },
    { href: "/decisions", label: "החלטות", icon: icons.decisions },
  ];
  const inTabs = tabs.some((tab) => active(tab.href));
  const title = groups.flatMap((group) => group.items).find((item) => active(item.href))?.label ?? "Beo-Trade";
  const phase = status ? PHASES[status.market_phase] || status.market_phase : "מתחבר";

  useEffect(() => {
    const label = () => {
      document.querySelectorAll<HTMLTableElement>("table.ops-cardify").forEach((table) => {
        const heads = Array.from(table.querySelectorAll("thead th")).map((cell) => cell.textContent?.trim() ?? "");
        table.querySelectorAll("tbody tr").forEach((row) => {
          Array.from(row.children).forEach((cell, index) => {
            const text = heads[index];
            if (text && cell.getAttribute("data-label") !== text) cell.setAttribute("data-label", text);
          });
        });
      });
    };
    label();
    const observer = new MutationObserver(label);
    observer.observe(document.body, { childList: true, subtree: true });
    return () => observer.disconnect();
  }, []);

  return (
    <DeskContext.Provider value={{ query, status: shown }}>
      <div className="aura" />
      <Toasts items={live.toasts} />
      <div className="desk">
        {menu ? <button type="button" className="rail-backdrop" aria-label="סגור תפריט" onClick={() => setMenu(false)} /> : null}
        <aside className={`rail${menu ? " open" : ""}`} aria-label="ניווט">
          <div className="sheet-top">
            <i className="sheet-grip" aria-hidden />
            <b>כל המסכים</b>
            <button type="button" className="rail-close" onClick={() => setMenu(false)}>סגור</button>
          </div>
          <div className="brand-block">
            <LogoLockup />
            <span className={`conn ${link}`}>
              <Heartbeat live={link === "up"} />
              {link === "up" ? copy.shell.connected : link === "down" ? copy.shell.disconnected : copy.common.connecting}
            </span>
            <span className="paper-flag">PAPER TRADING</span>
            <Link href="/versions" className="version-link">
              {release?.current ? `גרסה ${release.current}` : "גרסאות"}
            </Link>
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
                    onMouseEnter={() => warm(WARM[item.href] ?? [])}
                    onFocus={() => warm(WARM[item.href] ?? [])}
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
          <div className="sheet-actions">
            <button type="button" className="halt-btn run" disabled={closing} onClick={closePositions}>
              {closing ? copy.shell.closing : copy.shell.closeAllLong}
            </button>
          </div>
        </aside>
        <div className="stage">
          <header className="mobile-bar">
            <div className="mobile-title">
              <b>{title}</b>
              <span>
                <i className={`dot ${link}`} />
                {phase}
                <MobileClock />
              </span>
            </div>
            <button
              type="button"
              className={`mobile-halt ${halted ? "halted" : ""}`}
              aria-pressed={halted}
              disabled={switching}
              onClick={toggleTrading}
            >
              {switching ? copy.shell.halting : halted ? copy.shell.halted : copy.shell.halt}
            </button>
          </header>
          <header className="instrument">
            <div className="bar-item">
              <div>
                <div className="kicker">{copy.shell.market}</div>
                <b>{status ? PHASES[status.market_phase] || status.market_phase : "מתחבר"}</b>
              </div>
              <div className="session-track" aria-hidden>
                <div className="session-fill" style={{ width: `${progress}%` }} />
              </div>
            </div>
            <Clock timeZone="America/New_York" label={copy.shell.ny} />
            <Clock timeZone="Asia/Dubai" label={copy.shell.dubai} />
            <div className="bar-actions">
              <button
                type="button"
                className={`halt-btn ${halted ? "halted" : "stop"}`}
                aria-pressed={halted}
                disabled={switching}
                onClick={toggleTrading}
              >
                {switching ? copy.shell.halting : halted ? copy.shell.resume : copy.shell.halt}
              </button>
              <button type="button" className="halt-btn ghost" onClick={() => setFinder((open) => !open)}>
                {copy.shell.search}
              </button>
              <button type="button" className="halt-btn run" disabled={closing} onClick={closePositions}>
                {closing ? copy.shell.closing : copy.shell.closeAll}
              </button>
              <button
                type="button"
                className="halt-btn run"
                onClick={() => fetch("/api/auth/logout", { method: "POST" }).finally(() => (window.location.href = "/login"))}
              >
                {copy.shell.logout}
              </button>
            </div>
          </header>
          {finder ? (
            <div className="search-pop">
              <input autoFocus value={query} onChange={(event) => setQuery(event.target.value)} placeholder={copy.shell.search} aria-label={copy.shell.search} />
              {groups.flatMap((group) => group.items).filter((item) => item.label.includes(query.trim())).map((item) => (
                <Link key={item.href} href={item.href} onClick={() => setFinder(false)}>{item.label}</Link>
              ))}
            </div>
          ) : null}
          <Ticker
            quotes={(live.snap?.quotes || []).map((quote) => ({
              symbol: quote.symbol,
              price: num(quote.price),
              change: num(quote.change_percent),
            }))}
            series={live.series}
          />
          {note ? <p className="px-6 pt-3 text-sm text-[var(--down)]">{note}</p> : null}
          {live.stale ? <p className="stale-note">{copy.common.stale(Math.max(1, Math.round(((Date.now() - (live.updatedAt || Date.now())) / 1000))))}</p> : null}
          <motion.div
            className="stage-body"
            key={path}
            initial={reduce ? false : { opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            transition={reduce ? { duration: 0 } : { duration: 0.22 }}
          >
            {children}
          </motion.div>
        </div>
        <MobileTabBar>
          {tabs.map((tab) => (
            <Link key={tab.href} href={tab.href} className={`tab${active(tab.href) ? " on" : ""}`} onMouseEnter={() => warm(WARM[tab.href] ?? [])} onFocus={() => warm(WARM[tab.href] ?? [])}>
              <TabIcon d={tab.icon} />
              <span>{tab.label}</span>
            </Link>
          ))}
          <button type="button" className={`tab${menu || !inTabs ? " on" : ""}`} onClick={() => setMenu(true)}>
            <TabIcon d="M5 12h.01M12 12h.01M19 12h.01" />
            <span>עוד</span>
          </button>
        </MobileTabBar>
      </div>
    </DeskContext.Provider>
  );
}
