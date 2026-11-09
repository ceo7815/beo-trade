"use client";

import { useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";
import Link from "next/link";
import { apiGet } from "@/lib/api";
import { readStoredPeriod, writeStoredPeriod, type TradingPeriodRow } from "@/lib/account";
import { activePeriodId, badgeOf, menuIndex, storedChoice } from "@/lib/accountMenu";
import { useReduced } from "@/components/live-ui";

type PeriodsBody = { current_id?: string; periods?: TradingPeriodRow[] };

const FALLBACK: TradingPeriodRow[] = [
  { id: "paper_100k", label: "פייפר 100K", read_only: true, trading: false },
  { id: "paper_10k_ils", label: "פייפר 10K ₪", read_only: true, trading: false, retired: true, view_mode: "unavailable" },
  { id: "paper_20k_ils", label: "פייפר 20K ₪", trading: true, read_only: false },
  { id: "spy_intraday_20k", label: "SPY תוך-יומי 20K", read_only: true, trading: false, retired: true, view_mode: "unavailable" },
];

export function AccountSwitcher() {
  const [period, setPeriod] = useState("current");
  const [rows, setRows] = useState<TradingPeriodRow[]>([]);
  const [currentId, setCurrentId] = useState("paper_20k_ils");
  const [open, setOpen] = useState(false);
  const [cursor, setCursor] = useState(0);
  const [box, setBox] = useState<{ top: number; left: number; width: number; maxHeight: number } | null>(null);
  const [mounted, setMounted] = useState(false);
  const [failed, setFailed] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const reduced = useReduced();
  const listId = useId();

  useEffect(() => {
    setMounted(true);
    setPeriod(readStoredPeriod());
    let alive = true;
    apiGet<PeriodsBody>("/api/v1/trading-periods")
      .then((body) => {
        if (!alive) return;
        setRows(body.periods || []);
        setCurrentId(body.current_id || "paper_20k_ils");
      })
      .catch(() => {
        if (alive) setFailed(true);
      });
    return () => {
      alive = false;
    };
  }, []);

  const list = rows.length ? rows : failed ? FALLBACK : [];
  const activeId = activePeriodId(period, currentId);
  const active = list.find((row) => row.id === activeId) ?? list[0];
  const activeIndex = Math.max(0, list.findIndex((row) => row.id === activeId));

  function place() {
    const node = buttonRef.current;
    if (!node) return;
    const rect = node.getBoundingClientRect();
    const width = Math.min(320, Math.max(rect.width, 248));
    const rtl = document.documentElement.dir === "rtl";
    const rawLeft = rtl ? rect.right - width : rect.left;
    const left = Math.max(8, Math.min(rawLeft, window.innerWidth - width - 8));
    const top = rect.bottom + 8;
    const maxHeight = Math.max(160, Math.min(360, window.innerHeight - top - 16));
    setBox({ top, left, width, maxHeight });
  }

  useEffect(() => {
    if (!open) return;
    place();
    setCursor(activeIndex);
    const onPlace = () => place();
    window.addEventListener("resize", onPlace);
    window.addEventListener("scroll", onPlace, true);
    const onDoc = (event: MouseEvent) => {
      const target = event.target as Node;
      if (rootRef.current?.contains(target)) return;
      if ((target as HTMLElement).closest?.(".acct-pop")) return;
      setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setOpen(false);
        buttonRef.current?.focus();
      }
    };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    const focusTimer = window.setTimeout(() => {
      document.getElementById(`${listId}-${activeIndex}`)?.focus();
    }, 0);
    return () => {
      window.clearTimeout(focusTimer);
      window.removeEventListener("resize", onPlace);
      window.removeEventListener("scroll", onPlace, true);
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [open, activeIndex, listId]);

  function select(id: string) {
    const next = storedChoice(id, currentId);
    setOpen(false);
    if (next === period || (next === "current" && period === "current" && id === currentId)) {
      buttonRef.current?.focus();
      return;
    }
    setPeriod(next);
    writeStoredPeriod(next);
    window.location.reload();
  }

  function onButtonKey(event: React.KeyboardEvent) {
    if (event.key === "ArrowDown" || event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      setOpen(true);
    }
  }

  function onOptionKey(event: React.KeyboardEvent, index: number) {
    const next = menuIndex(index, list.length, event.key);
    if (next !== null) {
      event.preventDefault();
      setCursor(next);
      document.getElementById(`${listId}-${next}`)?.focus();
      return;
    }
    if (event.key === "Escape") {
      event.preventDefault();
      setOpen(false);
      buttonRef.current?.focus();
    }
    if (event.key === "Tab") setOpen(false);
  }

  const badge = active ? badgeOf(active) : "live";
  const badgeLabel = badge === "gone" ? "לא זמין" : badge === "archive" ? "ארכיון" : badge === "read" ? "קריאה" : "פעיל";
  const menu = open && box && mounted ? (
    <div
      className={`acct-pop${reduced ? " still" : ""}`}
      style={{ top: box.top, left: box.left, width: box.width, maxHeight: box.maxHeight }}
    >
      <div id={listId} role="listbox" aria-label="חשבון מסחר" className="acct-list lux-scroll">
        {list.map((row, index) => {
          const on = row.id === activeId;
          const kind = badgeOf(row);
          const label = kind === "gone" ? "לא זמין" : kind === "archive" ? "ארכיון" : kind === "read" ? "קריאה" : "פעיל";
          return (
            <button
              key={row.id}
              id={`${listId}-${index}`}
              type="button"
              role="option"
              aria-selected={on}
              className={`acct-option${on ? " on" : ""}${cursor === index ? " cursor" : ""}`}
              onClick={() => select(row.id)}
              onKeyDown={(event) => onOptionKey(event, index)}
              onMouseEnter={() => setCursor(index)}
            >
              <span>{row.label}</span>
              <i className={`acct-badge ${kind}`}>{label}</i>
            </button>
          );
        })}
      </div>
      {badge === "gone" ? <p className="acct-hint">החשבון לא זמין בברוקר. היסטוריה שמורה בלבד, בלי קריאה חיה.</p> : null}
      {badge === "read" ? <p className="acct-hint">תצוגת קריאה — בלי פקודות</p> : null}
      <Link href="/settings/rules" className="acct-rules" onClick={() => setOpen(false)}>
        חוקי מסחר
      </Link>
    </div>
  ) : null;

  return (
    <div className={`account-switch${open ? " open" : ""}`} ref={rootRef}>
      <button
        ref={buttonRef}
        type="button"
        className="acct-trigger"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={listId}
        onClick={() => setOpen((value) => !value)}
        onKeyDown={onButtonKey}
      >
        <span className="acct-name">{active?.label || "חשבון"}</span>
        <i className={`acct-badge ${badge}`}>{badgeLabel}</i>
        <svg className="acct-chevron" viewBox="0 0 12 12" width="12" height="12" aria-hidden="true">
          <path d="M2.5 4.5 6 8l3.5-3.5" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </button>
      {menu ? createPortal(menu, document.body) : null}
    </div>
  );
}
