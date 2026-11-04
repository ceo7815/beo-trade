/** Keyboard and badge rules for the account dropdown. No network. */

export function menuIndex(current: number, count: number, key: string): number | null {
  if (count <= 0) return null;
  if (key === "Home") return 0;
  if (key === "End") return count - 1;
  if (key === "ArrowDown") return (current + 1) % count;
  if (key === "ArrowUp") return (current - 1 + count) % count;
  return null;
}

export function storedChoice(id: string, currentId: string): string {
  return id === currentId ? "current" : id;
}

export function activePeriodId(stored: string, currentId: string): string {
  return !stored || stored === "current" ? currentId : stored;
}

export function badgeOf(row: { read_only?: boolean; trading?: boolean }): "read" | "live" {
  if (row.read_only || row.trading === false) return "read";
  return "live";
}
