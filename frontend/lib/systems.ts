/** Top-level desk systems. Avi remains a read-only archive at /avi and is not in the switcher. */

export type SystemId = "options" | "eli" | "options2" | "avi";

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
  { id: "options2", label: "אופציות 2", short: "אופציות 2", href: "/o2", badge: "נייר", statusPath: "/api/v1/system" },
];

const ARCHIVE: SystemRow = {
  id: "avi",
  label: "ארכיון אבי",
  short: "אבי",
  href: "/avi",
  badge: "ארכיון",
  statusPath: "/api/v1/avi/status",
};

export function systemFromPath(path: string): SystemId {
  if (path.startsWith("/o2")) return "options2";
  if (path.startsWith("/spy")) return "eli";
  if (path.startsWith("/avi")) return "avi";
  return "options";
}

export function systemById(id: SystemId): SystemRow {
  if (id === "avi") return ARCHIVE;
  return SYSTEMS.find((row) => row.id === id) ?? SYSTEMS[0];
}

/** Query wins, then the new key, then the old desk key (`spy` → אלי, `avi` → אופציות 2). */
export function parseStored(system: string | null, legacy: string | null, query: string | null): SystemId | null {
  if (query === "options" || query === "eli" || query === "options2") return query;
  if (query === "avi") return "options2";
  if (query === "spy" || query === "daily") return "eli";
  if (system === "options" || system === "eli" || system === "options2") return system;
  if (system === "avi") return "options2";
  if (legacy === "spy") return "eli";
  if (legacy === "options") return "options";
  return null;
}
