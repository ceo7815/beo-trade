"use client";

export type Protection = {
  status?: string | null;
  line?: string | null;
  kill?: string | null;
  pnl?: string | null;
  floor?: string | null;
  peak?: string | null;
  next_threshold?: string | null;
  next_floor?: string | null;
  distance?: string | null;
  protection_kind?: string | null;
  armed?: boolean;
};

function dollars(value: string | null | undefined) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "";
  const absolute = Math.abs(number);
  const body = Number.isInteger(absolute) ? absolute.toLocaleString("en-US") : absolute.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return number < 0 ? `−${body}$` : `${body}$`;
}

export function ProtectionBar({ protection }: { protection: Protection | null | undefined }) {
  if (!protection || protection.status === "unknown" || !protection.line) {
    return (
      <div className="protect" aria-label="הגנה לא ידועה">
        <p className="protect-line">לא ידוע</p>
      </div>
    );
  }
  const kill = Number(protection.kill);
  const pnl = Number(protection.pnl);
  const floor = Number(protection.floor);
  const next = Number(protection.next_threshold);
  const marks = [Number.isFinite(kill) ? -Math.abs(kill) : null, 0, Number.isFinite(pnl) ? pnl : null, Number.isFinite(floor) ? floor : null, Number.isFinite(next) ? next : null]
    .filter((value): value is number => value !== null);
  const min = Math.min(...marks);
  const max = Math.max(...marks);
  const span = max - min || 1;
  const at = (value: number) => `${((value - min) / span) * 100}%`;
  const killAt = Number.isFinite(kill) ? -Math.abs(kill) : null;
  const activeKill = protection.protection_kind === "kill";
  return (
    <div className="protect">
      <p className="protect-line">{protection.line}</p>
      <div className="protect-track" dir="ltr" aria-hidden="true">
        {killAt !== null ? <i className={`protect-mark kill${activeKill ? " active" : ""}`} style={{ insetInlineStart: at(killAt) }} /> : null}
        {Number.isFinite(floor) ? <i className={`protect-mark floor${protection.protection_kind === "floor" ? " active" : ""}`} style={{ insetInlineStart: at(floor) }} /> : null}
        {Number.isFinite(pnl) ? <i className="protect-mark now" style={{ insetInlineStart: at(pnl) }} /> : null}
        {Number.isFinite(next) ? <i className="protect-mark next" style={{ insetInlineStart: at(next) }} /> : null}
      </div>
      <div className="protect-scale" dir="ltr">
        <span className="kill">{killAt === null ? "—" : dollars(String(killAt))}</span>
        <span className="floor">{Number.isFinite(floor) ? dollars(protection.floor) : activeKill ? "בלם" : ""}</span>
        <span className="next">{Number.isFinite(next) ? dollars(protection.next_threshold) : ""}</span>
      </div>
    </div>
  );
}
