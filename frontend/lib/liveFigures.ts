/** Which headline figures may follow `/api/v1/stream`. Display only. */

export type LiveTransport = "connecting" | "sse" | "poll";

/** Read-only periods keep their own polled book. The stream is the live trading account. */
export function streamApplies(period: string | null | undefined): boolean {
  const value = (period || "current").trim();
  return value === "" || value === "current";
}

export function streamUrl(period: string | null | undefined): string {
  const value = (period || "current").trim() || "current";
  return `/backend/api/v1/stream?trading_period=${encodeURIComponent(value)}`;
}

export function pickHeadline(input: {
  period: string;
  transport: LiveTransport;
  stale?: boolean;
  equity?: string | null;
  displayPnl?: string | null;
  polledEquity?: string | null;
  polledPnl?: string | null;
}): { equity: string | null; dayPnl: string | null; live: boolean } {
  const tape = input.transport === "sse" && !input.stale && streamApplies(input.period);
  const equity = tape && filled(input.equity) ? input.equity : input.polledEquity;
  const dayPnl = tape && filled(input.displayPnl) ? input.displayPnl : input.polledPnl;
  return {
    equity: filled(equity) ? equity : null,
    dayPnl: filled(dayPnl) ? dayPnl : null,
    live: tape && (filled(input.equity) || filled(input.displayPnl)),
  };
}

function filled(value: string | null | undefined): value is string {
  return value != null && value !== "";
}
