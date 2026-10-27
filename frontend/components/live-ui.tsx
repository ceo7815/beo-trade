"use client";

import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { signedMoney, tone } from "@/lib/format";
import { copy } from "@/lib/copy";

export function Num({ children, className = "", title }: { children: ReactNode; className?: string; title?: string }) {
  return (
    <bdi dir="ltr" title={title} className={className ? `num ${className}` : "num"}>
      {children}
    </bdi>
  );
}

export function useTick(value: string | number | null | undefined) {
  const prev = useRef(value);
  const [dir, setDir] = useState<"" | "up" | "down">("");
  useEffect(() => {
    const before = prev.current;
    prev.current = value;
    const left = Number(before);
    const right = Number(value);
    if (before == null || value == null || !Number.isFinite(left) || !Number.isFinite(right) || left === right) return;
    setDir(right > left ? "up" : "down");
    const timer = window.setTimeout(() => setDir(""), 400);
    return () => window.clearTimeout(timer);
  }, [value]);
  return dir;
}

export function useReduced() {
  const [reduced, setReduced] = useState(false);
  useEffect(() => {
    const media = window.matchMedia("(prefers-reduced-motion: reduce)");
    const apply = () => setReduced(media.matches);
    apply();
    media.addEventListener("change", apply);
    return () => media.removeEventListener("change", apply);
  }, []);
  return reduced;
}

export function Odometer({ value, digits = 0, className = "" }: { value: number | null; digits?: number; className?: string }) {
  const ref = useRef<HTMLSpanElement>(null);
  const reduced = useReduced();
  useLayoutEffect(() => {
    const root = ref.current;
    if (!root) return;
    if (value === null) {
      root.textContent = "—";
      root.classList.remove("neg");
      return;
    }
    const text = signedMoney(value, digits);
    root.classList.toggle("neg", value < 0);
    if (reduced) {
      root.textContent = text;
      return;
    }
    const cells = Array.from(root.querySelectorAll<HTMLSpanElement>(":scope > .c"));
    if (cells.length !== text.length) {
      root.replaceChildren();
      for (const ch of text) {
        const cell = document.createElement("span");
        cell.className = "c";
        if (/\d/.test(ch)) {
          cell.classList.add("d");
          const col = document.createElement("span");
          col.className = "col";
          for (const n of "0123456789") {
            const digit = document.createElement("span");
            digit.textContent = n;
            col.appendChild(digit);
          }
          cell.appendChild(col);
        } else {
          cell.textContent = ch;
          if (ch === "$" || ch === "+" || ch === "−") cell.classList.add("cur");
        }
        root.appendChild(cell);
      }
    }
    Array.from(root.querySelectorAll<HTMLSpanElement>(":scope > .c")).forEach((cell, index) => {
      const ch = text[index];
      if (cell.classList.contains("d")) {
        const col = cell.querySelector<HTMLSpanElement>(".col");
        if (!col) return;
        const prev = col.dataset.v;
        col.style.transform = `translateY(-${Number(ch)}em)`;
        col.dataset.v = ch;
        if (prev !== undefined && prev !== ch) {
          cell.classList.remove("hit");
          void cell.offsetWidth;
          cell.classList.add("hit");
        }
      } else if (cell.textContent !== ch) {
        cell.textContent = ch;
      }
    });
  }, [value, digits, reduced]);
  return <span ref={ref} className={`odo ${className}`} dir="ltr" />;
}

export function Sparkline({ points, up }: { points: number[]; up: boolean }) {
  const width = 120;
  const height = 30;
  if (points.length < 2) return <svg className="spk" viewBox={`0 0 ${width} ${height}`} aria-hidden="true" />;
  const min = Math.min(...points);
  const max = Math.max(...points);
  const span = max - min || 1;
  const pts = points.map((value, index) => {
    const x = (index / (points.length - 1)) * width;
    const y = 27 - ((value - min) / span) * 22;
    return `${x.toFixed(1)} ${y.toFixed(1)}`;
  });
  const line = `M${pts.join(" L")}`;
  const color = up ? "var(--up)" : "var(--down)";
  const last = pts[pts.length - 1].split(" ");
  return (
    <svg className="spk" viewBox={`0 0 ${width} ${height}`} aria-hidden="true">
      <path d={`${line} L${width} ${height} L0 ${height} Z`} fill={color} opacity="0.16" />
      <path d={line} fill="none" stroke={color} strokeWidth="1.6" />
      <circle cx={last[0]} cy={last[1]} r="2.2" fill={color} />
    </svg>
  );
}

export function Heartbeat({ live = true }: { live?: boolean }) {
  return (
    <span className={`hb${live ? "" : " off"}`} aria-hidden="true">
      <i /><i /><i /><i /><i /><i />
    </span>
  );
}

export function Radar({ names, active }: { names: string[]; active: boolean }) {
  const dots = (names.length ? names : active ? ["", "", ""] : []).slice(0, 6);
  return (
    <div className={`radar${active ? " on" : ""}`} aria-hidden="true">
      {dots.map((name, index) => (
        <i key={`${name}-${index}`} className={index === 0 && active ? "hit" : ""} style={{ left: `${22 + ((index * 37) % 58)}%`, top: `${28 + ((index * 23) % 48)}%` }} />
      ))}
    </div>
  );
}

export function Gauge({ pnl, kill }: { pnl: number | null; kill: number | null }) {
  const span = kill && kill > 0 ? kill : null;
  const ratio = pnl === null || span === null ? 0 : Math.max(-1, Math.min(1, pnl / span));
  const at = ((ratio + 1) / 2) * 100;
  const loss = pnl !== null && pnl < 0 ? -pnl : 0;
  const near = span !== null && loss >= span * 0.7 && loss < span;
  const hot = span !== null && loss >= span;
  return (
    <div className={`gauge${near ? " near" : ""}${hot ? " hot" : ""}`} dir="ltr">
      <div className="gauge-top">
        <span>{copy.kill.gauge}</span>
        <Num className={tone(pnl)}>{pnl === null ? "—" : signedMoney(pnl, 0)}</Num>
      </div>
      <div className="track">
        <b className="knob" style={{ insetInlineStart: `${at}%` }} />
      </div>
      <div className="gauge-scale">
        <Num>{span === null ? "—" : signedMoney(-span, 0)}</Num>
        <Num>$0</Num>
        <Num>{span === null ? "—" : signedMoney(span, 0)}</Num>
      </div>
    </div>
  );
}

export function KillBanner({ pnl, kill, state }: { pnl: number | null; kill: number | null; state?: string | null }) {
  if (pnl === null || kill === null || kill <= 0) return null;
  const loss = pnl < 0 ? -pnl : 0;
  const tripped = state === "hard_stop" || loss >= kill;
  const near = !tripped && loss >= kill * 0.7;
  if (!near && !tripped) return null;
  return (
    <div className={tripped ? "kill-full" : "kill-banner"} role="status">
      <b>{tripped ? copy.kill.tripped : copy.kill.near}</b>
      <Num>{copy.kill.nearDetail(signedMoney(pnl, 0), signedMoney(-kill, 0))}</Num>
      {tripped ? <span>{copy.kill.trippedDetail}</span> : null}
    </div>
  );
}

export function Ticker({ quotes, series }: { quotes: { symbol: string; price: number | null; change: number | null }[]; series: Record<string, number[]> }) {
  const reduced = useReduced();
  if (quotes.length === 0) return null;
  const row = reduced ? quotes : [...quotes, ...quotes];
  return (
    <div className="ticker" dir="ltr">
      <div className={`tk-track${reduced ? " still" : ""}`}>
        {row.map((quote, index) => {
          const up = (quote.change ?? 0) >= 0;
          const flash = quote.change === null ? "" : up ? "hot" : "cold";
          return (
            <span className={`tk ${flash}`} key={`${quote.symbol}-${index}`}>
              <b>{quote.symbol}</b>
              <Num>{quote.price === null ? "—" : quote.price.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</Num>
              <Num className={up ? "up" : "down"}>{quote.change === null ? "" : `${up ? "▲ +" : "▼ −"}${Math.abs(quote.change).toFixed(2)}%`}</Num>
              <Sparkline points={series[`q:${quote.symbol}`] || []} up={up} />
            </span>
          );
        })}
      </div>
    </div>
  );
}

export function Toasts({ items }: { items: { id: string; title: string; tone: string }[] }) {
  return (
    <div id="toasts" aria-live="polite">
      {items.map((item) => (
        <div key={item.id} className={`toast3 in ${item.tone}`}>
          <b>{item.title}</b>
          <span className="bar-t"><i /></span>
        </div>
      ))}
    </div>
  );
}
