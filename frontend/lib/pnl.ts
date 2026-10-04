import type { PaperAccount } from "./api";

export const MARKS_KEY = "beo-trade-marks";

export function readMarks(): Record<string, string> {
  if (typeof window === "undefined") return {};
  try {
    return JSON.parse(localStorage.getItem(MARKS_KEY) || "{}") as Record<string, string>;
  } catch {
    return {};
  }
}

export function writeMarks(marks: Record<string, string>) {
  localStorage.setItem(MARKS_KEY, JSON.stringify(marks));
  window.dispatchEvent(new Event("beo-marks"));
}

function num(value: string | null | undefined) {
  const parsed = Number(value ?? 0);
  return Number.isFinite(parsed) ? parsed : 0;
}

export function portfolio(account: PaperAccount, marks: Record<string, string>) {
  const start = num(account.starting_cash);
  const cash = num(account.cash);
  let realized = 0;
  let openCost = 0;
  let openValue = 0;
  for (const trade of account.trades) {
    const entry = num(trade.entry_price);
    const cost = entry * trade.quantity * 100;
    if (trade.exit_price) {
      realized += num(trade.pnl_dollars);
    } else {
      openCost += cost;
      const mark = num(marks[trade.trade_id] || trade.entry_price);
      openValue += mark * trade.quantity * 100;
    }
  }
  const equity = cash + openValue;
  const profit = equity - start;
  const percent = start ? (profit / start) * 100 : 0;
  return { start, cash, realized, openCost, openValue, equity, profit, percent };
}

export function contractResult(entry: number, exit: number, contracts: number) {
  const invested = entry * contracts * 100;
  const profit = (exit - entry) * contracts * 100;
  const percent = entry ? ((exit - entry) / entry) * 100 : 0;
  return { invested, profit, percent };
}

export function money(value: number) {
  const sign = value < 0 ? "-" : "";
  return `${sign}$${Math.abs(value).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

export function percent(value: number) {
  const sign = value > 0 ? "+" : "";
  return `${sign}${value.toFixed(2)}%`;
}
