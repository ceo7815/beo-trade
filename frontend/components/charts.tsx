"use client";

import { motion } from "framer-motion";
import { useReduced } from "@/components/live-ui";

export type CurvePoint = { label?: string; value: number };
export type BarPoint = { label: string; value: number };

function money(value: number) {
  const body = Math.abs(value).toLocaleString("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 });
  if (value > 0) return `+${body}`;
  if (value < 0) return `−${body}`;
  return body;
}

function pathOf(values: number[], width: number, height: number) {
  if (!values.length) return { d: "", area: "" };
  const min = Math.min(...values, 0);
  const max = Math.max(...values, 0);
  const span = max - min || 1;
  const coords = values.map((value, index) => {
    const x = values.length === 1 ? width / 2 : (index / (values.length - 1)) * width;
    const y = 10 + (1 - (value - min) / span) * (height - 20);
    return [x, y] as const;
  });
  const d = coords.map(([x, y], index) => `${index ? "L" : "M"}${x.toFixed(1)} ${y.toFixed(1)}`).join(" ");
  const last = coords[coords.length - 1][0];
  const first = coords[0][0];
  return { d, area: `${d} L${last.toFixed(1)} ${height} L${first.toFixed(1)} ${height} Z` };
}

export function EquityCurve({ points, label = "עקומת הון" }: { points: CurvePoint[]; label?: string }) {
  const reduced = useReduced();
  const values = points.map((point) => point.value).filter((value) => Number.isFinite(value));
  if (values.length < 2) return <p className="chart-empty">העקומה תופיע כשיהיו לפחות שתי נקודות.</p>;
  const width = 720;
  const height = 168;
  const { d, area } = pathOf(values, width, height);
  const last = values[values.length - 1];
  return (
    <figure className="beo-chart-card">
      <figcaption>
        <span>{label}</span>
        <b className={last < 0 ? "down" : "up"}>{money(last)}</b>
      </figcaption>
      <svg className="beo-chart" viewBox={`0 0 ${width} ${height}`} role="img" aria-label={label}>
        <defs>
          <linearGradient id="beoFill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stopColor="rgba(231,201,138,0.38)" />
            <stop offset="1" stopColor="rgba(231,201,138,0)" />
          </linearGradient>
        </defs>
        <path d={area} className="area" />
        <path d={d} className={`line${reduced ? " still" : ""}`} pathLength={1} />
      </svg>
    </figure>
  );
}

export function DailyBars({ rows, label = "רווח והפסד יומי" }: { rows: BarPoint[]; label?: string }) {
  const reduced = useReduced();
  const usable = rows.filter((row) => Number.isFinite(row.value));
  if (!usable.length) return <p className="chart-empty">אין עמודות יומיות עדיין.</p>;
  const shown = usable.slice(-48);
  const peak = Math.max(...shown.map((row) => Math.abs(row.value)), 1);
  const width = Math.max(280, shown.length * 14);
  const height = 140;
  const mid = height / 2;
  return (
    <figure className="beo-chart-card">
      <figcaption>
        <span>{label}</span>
        <b>{shown.length} ימים</b>
      </figcaption>
      <svg className="beo-chart bars" viewBox={`0 0 ${width} ${height}`} role="img" aria-label={label}>
        <line x1="0" y1={mid} x2={width} y2={mid} className="axis" />
        {shown.map((row, index) => {
          const bar = (Math.abs(row.value) / peak) * (mid - 10);
          const y = row.value >= 0 ? mid - bar : mid;
          return (
            <motion.rect
              key={`${row.label}-${index}`}
              x={index * 14 + 3}
              width={8}
              rx={2}
              className={row.value < 0 ? "down" : "up"}
              initial={reduced ? false : { height: 0, y: mid }}
              animate={{ height: Math.max(bar, 1), y }}
              transition={reduced ? { duration: 0 } : { duration: 0.55, delay: Math.min(index, 24) * 0.012, ease: [0.16, 1, 0.3, 1] }}
            >
              <title>{`${row.label} ${money(row.value)}`}</title>
            </motion.rect>
          );
        })}
      </svg>
    </figure>
  );
}

export function StrategyBars({ rows, label = "לפי אסטרטגיה" }: { rows: BarPoint[]; label?: string }) {
  const reduced = useReduced();
  const shown = rows
    .filter((row) => Number.isFinite(row.value) && row.label)
    .sort((a, b) => Math.abs(b.value) - Math.abs(a.value))
    .slice(0, 6);
  if (!shown.length) return <p className="chart-empty">אין פירוק לפי אסטרטגיה עדיין.</p>;
  const peak = Math.max(...shown.map((row) => Math.abs(row.value)), 1);
  return (
    <figure className="beo-chart-card">
      <figcaption>
        <span>{label}</span>
      </figcaption>
      <ul className="strategy-bars">
        {shown.map((row, index) => (
          <li key={row.label}>
            <span>{row.label}</span>
            <i>
              <motion.b
                className={row.value < 0 ? "down" : "up"}
                initial={reduced ? false : { width: 0 }}
                animate={{ width: `${Math.max(6, (Math.abs(row.value) / peak) * 100)}%` }}
                transition={reduced ? { duration: 0 } : { duration: 0.6, delay: index * 0.05 }}
              />
            </i>
            <strong className={row.value < 0 ? "down" : "up"}>{money(row.value)}</strong>
          </li>
        ))}
      </ul>
    </figure>
  );
}

export function tradesToCharts(trades: { at?: string | null; pnl?: string | number | null; strategy?: string | null }[]) {
  const byDay = new Map<string, number>();
  const byStrategy = new Map<string, number>();
  for (const trade of trades) {
    const pnl = typeof trade.pnl === "number" ? trade.pnl : Number(trade.pnl);
    if (!Number.isFinite(pnl)) continue;
    const day = (trade.at || "").slice(0, 10) || "—";
    byDay.set(day, (byDay.get(day) || 0) + pnl);
    const strategy = (trade.strategy || "אחר").trim() || "אחר";
    byStrategy.set(strategy, (byStrategy.get(strategy) || 0) + pnl);
  }
  const days = [...byDay.entries()].sort(([a], [b]) => a.localeCompare(b));
  let running = 0;
  const curve: CurvePoint[] = days.map(([label, value]) => {
    running += value;
    return { label, value: running };
  });
  return {
    curve,
    days: days.map(([label, value]) => ({ label, value })),
    strategies: [...byStrategy.entries()].map(([label, value]) => ({ label, value })),
  };
}
