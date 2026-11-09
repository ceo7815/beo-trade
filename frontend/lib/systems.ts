/** Top-level desk systems. Avi is the QQQ debit-spread paper engine. */

export type SystemId = "options" | "eli" | "avi";

export type SystemRow = {
  id: SystemId;
  label: string;
  short: string;
  href: string;
  badge: string;
  statusPath: string | null;
};

export const SYSTEMS: SystemRow[] = [
  { id: "options", label: "אופציות", short: "אופציות", href: "/", badge: "ראשי", statusPath: "/api/v1/system" },
  { id: "eli", label: "מסחר יומי אלי", short: "יומי אלי", href: "/spy", badge: "נייר", statusPath: "/api/v1/spy/status" },
  { id: "avi", label: "מסחר אופציות אבי", short: "אבי", href: "/avi", badge: "נייר", statusPath: "/api/v1/avi/status" },
];

export function systemFromPath(path: string): SystemId {
  if (path.startsWith("/spy")) return "eli";
  if (path.startsWith("/avi")) return "avi";
  return "options";
}

export function systemById(id: SystemId): SystemRow {
  return SYSTEMS.find((row) => row.id === id) ?? SYSTEMS[0];
}

/** Query wins, then the new key, then the old desk key (`spy` → אלי). */
export function parseStored(system: string | null, legacy: string | null, query: string | null): SystemId | null {
  if (query === "options" || query === "eli" || query === "avi") return query;
  if (query === "spy" || query === "daily") return "eli";
  if (system === "options" || system === "eli" || system === "avi") return system;
  if (legacy === "spy") return "eli";
  if (legacy === "options") return "options";
  return null;
}
