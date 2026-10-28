export type RegimeStatus = {
  status?: string | null;
  market_regime?: string | null;
  trend_state?: string | null;
  volatility_state?: string | null;
  risk_state?: string | null;
  updated_at?: string | null;
};

export function regimeLabel(regime: RegimeStatus | null | undefined): string {
  if (!regime || regime.status !== "CALCULATED" || !regime.market_regime) return "ממתין לנתוני שוק";
  return regime.market_regime;
}

export function updatedAgo(iso: string | null | undefined, now = Date.now()): string | null {
  if (!iso) return null;
  const then = new Date(iso).getTime();
  if (!Number.isFinite(then)) return null;
  const seconds = Math.max(0, Math.round((now - then) / 1000));
  if (seconds < 60) return `עודכן לפני ${seconds} שנ׳`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `עודכן לפני ${minutes} דק׳`;
  const hours = Math.floor(minutes / 60);
  return `עודכן לפני ${hours} שע׳`;
}

export function regimeLine(regime: RegimeStatus | null | undefined, now = Date.now()): string {
  const label = regimeLabel(regime);
  const ago = updatedAgo(regime?.updated_at, now);
  return ago ? `${label} · ${ago}` : label;
}
