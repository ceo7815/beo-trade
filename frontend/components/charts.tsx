"use client";

import { useEffect, useId, useState, type ReactNode } from "react";
import { Area, Bar, CartesianGrid, Cell, ComposedChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

export type CurvePoint = { label?: string; value: number };
export type BarPoint = { label: string; value: number };

const tip = {
  borderRadius: 12,
  border: "1px solid var(--chart-line, rgba(29, 67, 51, 0.12))",
  background: "var(--chart-paper, #fff)",
  color: "var(--chart-ink, #1a1a1a)",
  fontSize: 12,
  boxShadow: "0 8px 24px rgb(28 28 27 / 8%)",
};

function money(value: number) {
  const body = Math.abs(value).toLocaleString("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 });
  if (value > 0) return `+${body}`;
  if (value < 0) return `−${body}`;
  return body;
}

function useDraw() {
  const [draw, setDraw] = useState(false);
  useEffect(() => {
    setDraw(!window.matchMedia("(prefers-reduced-motion: reduce)").matches);
  }, []);
  return draw;
}

function Frame({ label, aside, children, tall = false }: { label: string; aside?: string; children: ReactNode; tall?: boolean }) {
  return (
    <figure className="beo-chart-card">
      <figcaption>
        <span>{label}</span>
        {aside ? <b>{aside}</b> : null}
      </figcaption>
      <div className={`beo-chart-frame${tall ? " tall" : ""}`} dir="ltr">
        {children}
      </div>
    </figure>
  );
}

export function EquityCurve({ points, label = "עקומת הון" }: { points: CurvePoint[]; label?: string }) {
  const draw = useDraw();
  const rawId = useId().replace(/:/g, "");
  const values = points.filter((point) => Number.isFinite(point.value));
  if (values.length < 2) return <p className="chart-empty">העקומה תופיע כשיהיו לפחות שתי נקודות.</p>;
  const data = values.map((point, index) => ({ label: point.label || String(index + 1), value: point.value }));
  const last = data[data.length - 1].value;
  return (
    <Frame label={label} aside={money(last)} tall>
      <ResponsiveContainer width="100%" height="100%">
        <ComposedChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
          <defs>
            <linearGradient id={rawId} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="var(--chart-fill, #1d4333)" stopOpacity={0.42} />
              <stop offset="100%" stopColor="var(--chart-fill, #1d4333)" stopOpacity={0.04} />
            </linearGradient>
          </defs>
          <CartesianGrid stroke="var(--chart-grid, rgba(29,67,51,0.08))" vertical={false} />
          <XAxis dataKey="label" tick={{ fill: "var(--chart-tick, #6b6b66)", fontSize: 11 }} axisLine={false} tickLine={false} interval="preserveStartEnd" />
          <YAxis tick={{ fill: "var(--chart-tick, #6b6b66)", fontSize: 11 }} axisLine={false} tickLine={false} width={48} />
          <Tooltip cursor={{ stroke: "var(--chart-cursor, rgba(29,67,51,0.35))", strokeDasharray: "3 3" }} contentStyle={tip} formatter={(value) => [money(Number(value)), label]} />
          <Area type="monotone" dataKey="value" stroke="var(--chart-ink, #1d4333)" strokeWidth={2} fill={`url(#${rawId})`} isAnimationActive={draw} animationDuration={900} animationEasing="ease-out" />
        </ComposedChart>
      </ResponsiveContainer>
    </Frame>
  );
}

export function SparkChart({ points, label = "ניצוץ" }: { points: number[]; label?: string }) {
  const draw = useDraw();
  const rawId = useId().replace(/:/g, "");
  const data = points.filter((value) => Number.isFinite(value)).map((value, index) => ({ i: index, value }));
  if (data.length < 2) return null;
  return (
    <div className="beo-spark" dir="ltr" aria-label={label}>
      <ResponsiveContainer width="100%" height="100%">
        <ComposedChart data={data} margin={{ top: 4, right: 0, left: 0, bottom: 0 }}>
          <defs>
            <linearGradient id={rawId} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="var(--chart-fill, #1d4333)" stopOpacity={0.35} />
              <stop offset="100%" stopColor="var(--chart-fill, #1d4333)" stopOpacity={0} />
            </linearGradient>
          </defs>
          <Area type="monotone" dataKey="value" stroke="var(--chart-ink, #1d4333)" strokeWidth={1.6} fill={`url(#${rawId})`} isAnimationActive={draw} animationDuration={700} animationEasing="ease-out" />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}

export function DailyBars({ rows, label = "רווח והפסד יומי" }: { rows: BarPoint[]; label?: string }) {
  const draw = useDraw();
  const shown = rows.filter((row) => Number.isFinite(row.value)).slice(-48);
  if (!shown.length) return <p className="chart-empty">אין עמודות יומיות עדיין.</p>;
  return (
    <Frame label={label} aside={`${shown.length} ימים`}>
      <ResponsiveContainer width="100%" height="100%">
        <ComposedChart data={shown} margin={{ top: 8, right: 4, left: 0, bottom: 0 }}>
          <CartesianGrid stroke="var(--chart-grid, rgba(29,67,51,0.08))" vertical={false} />
          <XAxis dataKey="label" hide />
          <YAxis tick={{ fill: "var(--chart-tick, #6b6b66)", fontSize: 11 }} axisLine={false} tickLine={false} width={44} />
          <Tooltip cursor={{ fill: "rgba(29, 67, 51, 0.06)" }} contentStyle={tip} formatter={(value) => [money(Number(value)), label]} />
          <Bar dataKey="value" radius={[3, 3, 0, 0]} isAnimationActive={draw} animationDuration={900} animationEasing="ease-out">
            {shown.map((row) => (
              <Cell key={row.label} fill={row.value < 0 ? "var(--down, #c4382f)" : "var(--chart-fill, #1d4333)"} />
            ))}
          </Bar>
        </ComposedChart>
      </ResponsiveContainer>
    </Frame>
  );
}

export function StrategyBars({ rows, label = "לפי אסטרטגיה" }: { rows: BarPoint[]; label?: string }) {
  const draw = useDraw();
  const shown = rows
    .filter((row) => Number.isFinite(row.value) && row.label)
    .sort((a, b) => Math.abs(b.value) - Math.abs(a.value))
    .slice(0, 6);
  if (!shown.length) return <p className="chart-empty">אין פירוק לפי אסטרטגיה עדיין.</p>;
  return (
    <Frame label={label}>
      <ResponsiveContainer width="100%" height="100%">
        <ComposedChart data={shown} layout="vertical" margin={{ top: 4, right: 12, left: 8, bottom: 0 }}>
          <XAxis type="number" hide />
          <YAxis type="category" dataKey="label" width={72} tick={{ fill: "var(--chart-tick, #6b6b66)", fontSize: 12 }} axisLine={false} tickLine={false} />
          <Tooltip contentStyle={tip} formatter={(value) => [money(Number(value)), label]} />
          <Bar dataKey="value" radius={[0, 4, 4, 0]} isAnimationActive={draw} animationDuration={900} animationEasing="ease-out">
            {shown.map((row) => (
              <Cell key={row.label} fill={row.value < 0 ? "var(--down, #c4382f)" : "var(--chart-fill, #1d4333)"} />
            ))}
          </Bar>
        </ComposedChart>
      </ResponsiveContainer>
    </Frame>
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
