"use client";

import { useEffect, useRef, useState } from "react";

export function FloorMark({ closed, amount }: { closed: boolean; amount: string }) {
  const number = Number(amount);
  const label = Number.isFinite(number)
    ? number.toLocaleString("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 })
    : "";
  const text = closed ? "היום נסגר ברצפה" : "נפתחה הגנה";
  const previous = useRef<string | null>(null);
  const [justLocked, setJustLocked] = useState(false);
  useEffect(() => {
    const before = previous.current;
    previous.current = amount;
    if (before === null || !Number.isFinite(number) || !(Number(amount) > Number(before))) return;
    setJustLocked(true);
    const timer = window.setTimeout(() => setJustLocked(false), 900);
    return () => window.clearTimeout(timer);
  }, [amount, number]);
  return (
    <span className={`floor-mark${closed ? " closed" : ""}${justLocked ? " locked" : ""}`} aria-label={`${text} ${label}`}>
      <b>{closed ? "נסגר" : "הגנה"}</b>
      <small className="num">{label}</small>
    </span>
  );
}
