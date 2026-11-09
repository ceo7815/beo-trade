"use client";

import { useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { usePathname, useRouter } from "next/navigation";
import { menuIndex } from "@/lib/accountMenu";
import { useReduced } from "@/components/live-ui";

const STORAGE_KEY = "beo.desk";

const CHOICES = [
  { id: "options", label: "מסחר באופציות", badge: "פעיל", kind: "live" },
  { id: "spy", label: "מסחר יומי", badge: "נייר", kind: "read" },
] as const;

type DeskId = (typeof CHOICES)[number]["id"];

function storedDesk(): DeskId | null {
  if (typeof window === "undefined") return null;
  try {
    const query = new URLSearchParams(window.location.search).get("desk");
    if (query === "spy" || query === "daily") return "spy";
    if (query === "options") return "options";
    const saved = window.localStorage.getItem(STORAGE_KEY);
    if (saved === "spy" || saved === "options") return saved;
  } catch {
    return null;
  }
  return null;
}

function remember(id: DeskId) {
  try {
    window.localStorage.setItem(STORAGE_KEY, id);
    const url = new URL(window.location.href);
    url.searchParams.set("desk", id);
    window.history.replaceState({}, "", url);
  } catch {
    /* the route still holds the choice */
  }
}

export function DeskSwitcher() {
  const path = usePathname();
  const router = useRouter();
  const activeId: DeskId = path.startsWith("/spy") ? "spy" : "options";
  const [open, setOpen] = useState(false);
  const [cursor, setCursor] = useState(0);
  const [box, setBox] = useState<{ top: number; left: number; width: number; maxHeight: number } | null>(null);
  const [mounted, setMounted] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const reduced = useReduced();
  const listId = useId();
  const activeIndex = Math.max(0, CHOICES.findIndex((row) => row.id === activeId));
  const active = CHOICES[activeIndex] ?? CHOICES[0];

  useEffect(() => setMounted(true), []);

  useEffect(() => {
    if (path.startsWith("/spy")) {
      remember("spy");
      return;
    }
    const saved = storedDesk();
    if (path === "/" && saved === "spy") {
      router.replace("/spy");
      return;
    }
    remember("options");
  }, [path, router]);

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

  function select(id: DeskId) {
    setOpen(false);
    if (id === activeId) {
      buttonRef.current?.focus();
      return;
    }
    remember(id);
    router.push(id === "spy" ? "/spy" : "/");
  }

  function onButtonKey(event: React.KeyboardEvent) {
    if (event.key === "ArrowDown" || event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      setOpen(true);
    }
  }

  function onOptionKey(event: React.KeyboardEvent, index: number) {
    const next = menuIndex(index, CHOICES.length, event.key);
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

  const menu = open && box && mounted ? (
    <div
      className={`acct-pop${reduced ? " still" : ""}`}
      style={{ top: box.top, left: box.left, width: box.width, maxHeight: box.maxHeight }}
    >
      <div id={listId} role="listbox" aria-label="מערכת מסחר" className="acct-list lux-scroll">
        {CHOICES.map((row, index) => {
          const on = row.id === activeId;
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
              <i className={`acct-badge ${row.kind}`}>{row.badge}</i>
            </button>
          );
        })}
      </div>
    </div>
  ) : null;

  return (
    <div className={`account-switch desk-account${open ? " open" : ""}`} ref={rootRef}>
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
        <span className="acct-name">{active.label}</span>
        <i className={`acct-badge ${active.kind}`}>{active.badge}</i>
        <svg className="acct-chevron" viewBox="0 0 12 12" width="12" height="12" aria-hidden="true">
          <path d="M2.5 4.5 6 8l3.5-3.5" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </button>
      {menu ? createPortal(menu, document.body) : null}
    </div>
  );
}
