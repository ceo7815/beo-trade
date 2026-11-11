"use client";

import { useEffect } from "react";
import { usePathname, useRouter } from "next/navigation";
import { SYSTEMS, parseStored, systemById, systemFromPath, type SystemId } from "@/lib/systems";

const SYSTEM_KEY = "beo.system";
const LEGACY_KEY = "beo.desk";

function readSaved(): SystemId | null {
  if (typeof window === "undefined") return null;
  try {
    const query = new URLSearchParams(window.location.search).get("desk") || new URLSearchParams(window.location.search).get("system");
    return parseStored(window.localStorage.getItem(SYSTEM_KEY), window.localStorage.getItem(LEGACY_KEY), query);
  } catch {
    return null;
  }
}

function remember(id: SystemId) {
  try {
    window.localStorage.setItem(SYSTEM_KEY, id);
    const url = new URL(window.location.href);
    url.searchParams.delete("desk");
    url.searchParams.set("system", id);
    window.history.replaceState({}, "", url);
  } catch {
    /* the route still shows the choice */
  }
}

export function SystemSwitch() {
  const path = usePathname() || "/";
  const router = useRouter();
  const active = systemFromPath(path);

  useEffect(() => {
    if (path.startsWith("/spy") || path.startsWith("/o2")) {
      remember(systemFromPath(path));
      return;
    }
    if (path.startsWith("/avi")) return;
    if (path === "/") {
      const saved = readSaved();
      if (saved === "eli") router.replace("/spy");
      else if (saved === "options2") router.replace("/o2");
      else remember("options");
    }
  }, [path, router]);

  function select(id: SystemId) {
    if (id === active) return;
    remember(id);
    router.push(systemById(id).href);
  }

  return (
    <div className="system-switch" role="tablist" aria-label="מערכת מסחר">
      {SYSTEMS.map((row) => (
        <button
          key={row.id}
          type="button"
          role="tab"
          aria-selected={row.id === active}
          className={row.id === active ? "on" : ""}
          onClick={() => select(row.id)}
        >
          <b className="full">{row.label}</b>
          <b className="short">{row.short}</b>
          <small>{row.badge}</small>
        </button>
      ))}
    </div>
  );
}
