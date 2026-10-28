"use client";

import { money, signedMoney } from "@/lib/format";
import { copy } from "@/lib/copy";
import { finiteNumber, killRoom, markerModel, visibleLine } from "@/lib/protection";

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

function dollars(value: number) {
  const absolute = Math.abs(value);
  const whole = Math.round(absolute) === absolute;
  const body = whole
    ? absolute.toLocaleString("en-US", { maximumFractionDigits: 0 })
    : absolute.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return value < 0 ? `−${body}$` : `${body}$`;
}

export function ProtectionBar({ protection }: { protection: Protection | null | undefined }) {
  if (!protection || protection.status === "unknown" || !protection.line) {
    return (
      <div className="protect" aria-label="הגנה לא ידועה">
        <p className="protect-line">לא ידוע</p>
      </div>
    );
  }
  const kill = finiteNumber(protection.kill);
  const pnl = finiteNumber(protection.pnl);
  const floor = finiteNumber(protection.floor);
  const next = finiteNumber(protection.next_threshold);
  const peak = finiteNumber(protection.peak);
  const armed = Boolean(protection.armed) && floor !== null && floor > 0;
  const line = visibleLine(protection.line, pnl, armed ? peak ?? pnl : null, armed);
  const model = markerModel({ kill, pnl, floor, next, armed });
  const room = killRoom(pnl, kill);
  const zero = model.at(0);
  const track = `linear-gradient(90deg, var(--down) 0%, #6e2a32 ${Math.max(0, zero - 6)}%, #1b1f24 ${zero}%, #3a3426 78%, var(--gold) 100%)`;
  return (
    <div className="protect">
      <p className="protect-line">{line}</p>
      <div className="protect-rail" dir="ltr" aria-hidden="true">
        <div className="protect-track" style={{ background: track }} />
        {model.killAt !== null ? <span className="protect-dot kill" style={{ left: `${model.at(model.killAt)}%` }} /> : null}
        {model.floorAt !== null ? <span className="protect-dot floor" style={{ left: `${model.at(model.floorAt)}%` }} /> : null}
        {model.nextAt !== null ? <span className="protect-dot next" style={{ left: `${model.at(model.nextAt)}%` }} /> : null}
        {model.pnl !== null ? <span className="protect-dot now" style={{ left: `${model.at(model.pnl)}%` }} /> : null}
      </div>
      <div className="protect-scale" dir="ltr">
        <span className="kill">{model.killAt === null ? "" : dollars(model.killAt)}</span>
        <span className={model.floorAt !== null ? "floor" : "zero"}>{model.floorAt !== null ? dollars(model.floorAt) : "$0"}</span>
        <span className="next">{model.nextAt !== null ? dollars(model.nextAt) : ""}</span>
      </div>
      {room !== null ? (
        <p className="protect-distance">
          <span className="hint" title={copy.kill.distanceHint}>
            {copy.kill.distance}{" "}
            <bdi dir="ltr" className={room < 0 ? "down" : ""}>{room < 0 ? signedMoney(room, 0) : money(room, 0)}</bdi>
          </span>
          <small>{copy.kill.distanceSub}</small>
        </p>
      ) : null}
    </div>
  );
}
