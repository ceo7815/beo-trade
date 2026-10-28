export function unavailable(data: unknown): boolean {
  return Boolean(data) && typeof data === "object" && (data as { available?: boolean }).available === false;
}

/** Keep the last good screen. A failed read must not replace it. */
export function holdScreen<T extends { available?: boolean; stale?: boolean; as_of?: string | null }>(
  current: T | null,
  next: T | null | undefined,
): T | null {
  if (!next || next.available === false) {
    if (!current || current.available === false) return current;
    return { ...current, stale: true, as_of: current.as_of ?? new Date().toISOString() };
  }
  return next;
}
