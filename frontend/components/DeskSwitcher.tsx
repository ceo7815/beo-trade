"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

export function DeskSwitcher() {
  const path = usePathname();
  const spy = path.startsWith("/spy");
  return (
    <div className="desk-switch" role="tablist" aria-label="מערכת מסחר">
      <Link href="/" role="tab" aria-selected={!spy} className={spy ? "" : "on"}>
        אופציות
      </Link>
      <Link href="/spy" role="tab" aria-selected={spy} className={spy ? "on" : ""}>
        SPY תוך-יומי
      </Link>
    </div>
  );
}
