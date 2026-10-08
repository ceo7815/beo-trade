export const EXIT_REASONS: Record<string, string> = {
  SESSION_CLOSE: "סגירת יום מסחר",
  STOP: "סטופ הפסד",
  PROTECTED_STOP: "סטופ מוגן",
  TRAILING: "סטופ נגרר",
  TIME_STOP: "זמן ההחזקה הסתיים",
  EXPIRATION: "לפני פקיעה",
  INVALIDATION: "התזה בוטלה",
  LIQUIDITY: "נזילות ירדה",
  KILL_SWITCH: "עצירת חירום",
  CLOSE_ALL: "סגירת כל הפוזיציות",
};

export function exitLabel(reason: string | null | undefined) {
  if (!reason) return "—";
  return EXIT_REASONS[reason] || reason;
}
