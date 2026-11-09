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

export function badgeOf(row: { id?: string; read_only?: boolean; trading?: boolean; retired?: boolean; view_mode?: string }): "read" | "live" | "gone" | "archive" {
  if (row.id === "paper_100k") return "archive";
  if (row.retired || row.view_mode === "unavailable") return "gone";
  if (row.read_only || row.trading === false) return "read";
  return "live";
}
