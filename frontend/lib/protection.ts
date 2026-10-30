/** Same ladder as trading.toml. Display only. The server sends the sentence. */
const STEPS: Array<[number, number]> = [
  [4000, 2000],
  [7000, 3000],
  [8000, 4000],
  [9000, 5000],
  [10000, 6000],
  [11000, 7000],
  [12000, 8000],
  [13000, 9000],
  [14000, 10000],
  [15000, 11000],
];

export const FRESH_DAY_NOTE = "יום חדש — מתאפס בפתיחה";
export const NO_STAKE = "אין השקעה היום";

export function finiteNumber(value: string | number | null | undefined): number | null {
  if (value === null || value === undefined || value === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function cents(value: number) {
  return Math.round(Math.abs(value) * 100);
}

export function moneyWords(value: number) {
  const absolute = cents(value) / 100;
  const text = cents(value) % 100 === 0
    ? absolute.toLocaleString("en-US", { maximumFractionDigits: 0 })
    : absolute.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return `${text}$`;
}

function floorAtPeak(peak: number) {
  let found: number | null = null;
  for (const [arm, floor] of STEPS) {
    if (peak >= arm) found = floor;
  }
  return found;
}

function nextStep(mark: number) {
  for (const [arm, floor] of STEPS) {
    if (arm > mark) return { arm, floor };
  }
  return null;
}

function reached(pnl: number, arm: number) {
  return Math.round((arm - pnl) * 100) <= 0;
}

export function protectionLine(pnl: number, peak: number | null, armed: boolean) {
  const floor = armed && peak !== null ? floorAtPeak(peak) : null;
  const reference = floor !== null && peak !== null ? peak : pnl;
  let next = nextStep(reference);
  if (next && reached(pnl, next.arm)) next = nextStep(pnl);
  if (!next || reached(pnl, next.arm)) {
    return floor === null ? "לא ידוע" : `מוגנים ${moneyWords(floor)}`;
  }
  const distance = next.arm - pnl;
  if (floor === null) {
    return `עוד ${moneyWords(distance)} (ב-${moneyWords(next.arm)}) נכנסת הגנה של ${moneyWords(next.floor)}`;
  }
  return `מוגנים ${moneyWords(floor)} · עוד ${moneyWords(distance)} (ב-${moneyWords(next.arm)}) ההגנה עולה ל-${moneyWords(next.floor)}`;
}

export function previewLine(pnl: number) {
  const floor = floorAtPeak(pnl);
  return protectionLine(pnl, floor === null ? null : pnl, floor !== null);
}

export function visibleLine(serverLine: string | null | undefined, pnl: number | null, peak: number | null, armed: boolean) {
  const fallback = pnl === null ? "לא ידוע" : protectionLine(pnl, peak, armed);
  if (!serverLine || serverLine.includes("עוד 0$")) return fallback;
  return serverLine;
}

export function shownDayPnl(input: {
  display: number | null;
  mark: number | null;
  risk: number | null;
  note: string | null;
  closed: boolean;
  lastDay: number | null;
}) {
  if (input.note) {
    return { pnl: input.display ?? 0, note: input.note, showLast: false };
  }
  // Prefer display/risk (adjusted). Never let raw mark_pnl drive the desk when risk exists.
  const live = input.display !== null ? input.display : input.risk ?? input.mark;
  const showLast = input.closed && (live === null || live === 0) && input.lastDay !== null;
  return { pnl: showLast ? input.lastDay : live, note: null, showLast };
}

export function returnCaption(pct: number | null, note: string | null): { text: string; tone: "up" | "down" | "flat" | "muted" } {
  if (note) return { text: note, tone: "muted" };
  if (pct === null) return { text: NO_STAKE, tone: "muted" };
  const tone = pct > 0 ? "up" : pct < 0 ? "down" : "flat";
  const body = Math.abs(pct).toFixed(1);
  const text = pct > 0 ? `+${body}%` : pct < 0 ? `−${body}%` : `${body}%`;
  return { text: `${text} מההשקעה היום`, tone };
}

export function markerModel(input: {
  kill: number | null;
  pnl: number | null;
  floor: number | null;
  next: number | null;
  armed: boolean;
}) {
  const killAt = input.kill !== null && input.kill > 0 ? -Math.abs(input.kill) : null;
  const floorAt = input.armed && input.floor !== null && input.floor > 0 ? input.floor : null;
  const nextAt = input.next;
  const points = [killAt, 0, input.pnl, floorAt, nextAt].filter((value): value is number => value !== null);
  const min = Math.min(...points);
  const max = Math.max(...points);
  const span = max - min || 1;
  const at = (value: number) => ((value - min) / span) * 100;
  return { killAt, floorAt, nextAt, pnl: input.pnl, min, max, at };
}

export function killRoom(pnl: number | null, kill: number | null) {
  if (pnl === null || kill === null || kill <= 0) return null;
  return pnl + Math.abs(kill);
}
