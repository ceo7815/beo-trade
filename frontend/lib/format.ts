const MINUS = "−";

export function num(value: string | number | null | undefined) {
  if (value === null || value === undefined || value === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

export function money(value: number, digits = 2) {
  const abs = Math.abs(value).toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
  return `${value < 0 ? MINUS : ""}$${abs}`;
}

export function signedMoney(value: number, digits = 0) {
  const abs = Math.abs(value).toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
  if (value > 0) return `+$${abs}`;
  if (value < 0) return `${MINUS}$${abs}`;
  return `$${abs}`;
}

export function signedPct(value: number, digits = 1) {
  const body = Math.abs(value).toFixed(digits);
  if (value > 0) return `+${body}%`;
  if (value < 0) return `${MINUS}${body}%`;
  return `${body}%`;
}

export function tone(value: number | null) {
  if (value === null || value === 0) return "";
  return value > 0 ? "up" : "down";
}

export function nyCashOpen(date = new Date()) {
  const parts = new Intl.DateTimeFormat("en-GB", {
    timeZone: "America/New_York",
    weekday: "short",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).formatToParts(date);
  const pick = (type: string) => parts.find((part) => part.type === type)?.value ?? "";
  const weekday = pick("weekday");
  if (weekday === "Sat" || weekday === "Sun") return false;
  const minutes = Number(pick("hour")) * 60 + Number(pick("minute"));
  return minutes >= 9 * 60 + 30 && minutes < 16 * 60;
}

export function dubaiTime(date = new Date(), withSeconds = true) {
  return new Intl.DateTimeFormat("en-GB", {
    timeZone: "Asia/Dubai",
    hour: "2-digit",
    minute: "2-digit",
    second: withSeconds ? "2-digit" : undefined,
    hour12: false,
  }).format(date);
}

export function dubaiStamp(iso: string | null | undefined) {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  const day = new Intl.DateTimeFormat("en-GB", {
    timeZone: "Asia/Dubai",
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
  }).format(date);
  return `${day.replace(/\//g, ".")} · ${dubaiTime(date, false)}`;
}
