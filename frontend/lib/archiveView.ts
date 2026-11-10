/** Display helpers for the read-only Paper 100K archive. No trading actions. */

export const ARCHIVE_PERIOD = "paper_100k";
export const ARCHIVE_BADGE = "ארכיון · קריאה בלבד";

export function isArchivePeriod(period: string | null | undefined) {
  return (period || "") === ARCHIVE_PERIOD;
}

/** `2026-10-07` → `7.10`. Empty when the value is not a calendar day. */
export function dayDot(iso?: string | null) {
  if (!iso) return "";
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  if (!match) return "";
  return `${Number(match[3])}.${Number(match[2])}`;
}

export function archiveRangeLabel(start?: string | null, end?: string | null) {
  const left = dayDot(start);
  const right = dayDot(end);
  if (left && right) return `${left}–${right}`;
  return left || right;
}
