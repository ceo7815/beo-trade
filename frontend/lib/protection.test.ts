import assert from "node:assert/strict";
import test from "node:test";
import { finiteNumber, killRoom, markerModel, previewLine, protectionLine, returnCaption, shownDayPnl, visibleLine } from "./protection.ts";

test("protection line follows the ladder at the desk checkpoints", () => {
  assert.equal(previewLine(-1000), "עוד 5,000$ (ב-4,000$) נכנסת הגנה של 2,000$");
  assert.equal(previewLine(0), "עוד 4,000$ (ב-4,000$) נכנסת הגנה של 2,000$");
  assert.equal(previewLine(1000), "עוד 3,000$ (ב-4,000$) נכנסת הגנה של 2,000$");
  assert.equal(previewLine(4500), "מוגנים 2,000$ · עוד 2,500$ (ב-7,000$) ההגנה עולה ל-3,000$");
  assert.equal(previewLine(5500), "מוגנים 2,000$ · עוד 1,500$ (ב-7,000$) ההגנה עולה ל-3,000$");
  assert.equal(previewLine(7366), "מוגנים 3,000$ · עוד 634$ (ב-8,000$) ההגנה עולה ל-4,000$");
  assert.equal(previewLine(15000), "מוגנים 11,000$");
});

test("a P&L past the next step does not say עוד 0$ when the floor is not armed", () => {
  const line = protectionLine(4500, null, false);
  assert.equal(line, "עוד 2,500$ (ב-7,000$) נכנסת הגנה של 3,000$");
  assert.equal(line.includes("עוד 0$"), false);
  const stale = visibleLine("עוד 0$ (ב-4,000$) נכנסת הגנה של 2,000$", 7367, null, false);
  assert.equal(stale.includes("עוד 0$"), false);
  assert.equal(stale, "עוד 633$ (ב-8,000$) נכנסת הגנה של 4,000$");
});

test("pre-open stale broker P&L is shown as a fresh day, not yesterday's mark", () => {
  const day = shownDayPnl({
    display: 0,
    mark: 7367,
    risk: 7367,
    note: "יום חדש — מתאפס בפתיחה",
    closed: true,
    lastDay: 7367,
  });
  assert.equal(day.pnl, 0);
  assert.equal(day.showLast, false);
  assert.equal(day.note, "יום חדש — מתאפס בפתיחה");
  assert.equal(returnCaption(null, day.note).text, "יום חדש — מתאפס בפתיחה");
  assert.equal(previewLine(day.pnl), "עוד 4,000$ (ב-4,000$) נכנסת הגנה של 2,000$");
  assert.equal(killRoom(day.pnl, 3500), 3500);
});

test("a missing day return is a sentence, and a null floor is not a marker at zero", () => {
  assert.equal(returnCaption(null, null).text, "אין השקעה היום");
  assert.equal(returnCaption(null, null).tone, "muted");
  assert.equal(returnCaption(12.4, null).text, "+12.4% מההשקעה היום");
  assert.equal(finiteNumber(null), null);
  assert.equal(finiteNumber(""), null);
  const marks = markerModel({ kill: 3500, pnl: 7367, floor: null, next: 8000, armed: false });
  assert.equal(marks.floorAt, null);
  assert.equal(marks.killAt, -3500);
  assert.ok(marks.at(0) > 0 && marks.at(0) < 100);
  assert.ok(marks.at(7367) <= 100);
});
