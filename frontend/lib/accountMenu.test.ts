import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { activePeriodId, badgeOf, menuIndex, storedChoice } from "./accountMenu.ts";

test("arrows wrap the account list and Home/End jump", () => {
  assert.equal(menuIndex(0, 3, "ArrowUp"), 2);
  assert.equal(menuIndex(2, 3, "ArrowDown"), 0);
  assert.equal(menuIndex(1, 3, "Home"), 0);
  assert.equal(menuIndex(1, 3, "End"), 2);
  assert.equal(menuIndex(0, 0, "ArrowDown"), null);
  assert.equal(menuIndex(0, 3, "Enter"), null);
});

test("the trading account stores as current and a view stores its id", () => {
  assert.equal(storedChoice("paper_10k_ils", "paper_10k_ils"), "current");
  assert.equal(storedChoice("paper_100k", "paper_10k_ils"), "paper_100k");
  assert.equal(activePeriodId("current", "paper_10k_ils"), "paper_10k_ils");
  assert.equal(activePeriodId("paper_20k", "paper_10k_ils"), "paper_20k");
});

test("read-only views badge as קריאה and the trading account as פעיל", () => {
  assert.equal(badgeOf({ read_only: true }), "read");
  assert.equal(badgeOf({ read_only: false }), "live");
  assert.equal(badgeOf({ read_only: true, trading: false }), "read");
  assert.equal(badgeOf({ read_only: false, trading: false }), "read");
  assert.equal(badgeOf({ read_only: false, trading: true }), "live");
});

test("the history day figure is one line, not a grid of characters", () => {
  const css = [
    readFileSync(new URL("../app/globals.css", import.meta.url), "utf8"),
    readFileSync(new URL("../app/v2.css", import.meta.url), "utf8"),
    readFileSync(new URL("../app/glass.css", import.meta.url), "utf8"),
  ].join("\n");
  assert.match(css, /\.book-trigger > \.book-label/);
  assert.doesNotMatch(css, /\.book-trigger span\s*\{[^}]*display:\s*grid/);
  assert.match(css, /\.odo\s*\{[^}]*white-space:\s*nowrap/);
  assert.match(css, /\.line-num/);
  assert.match(css, /\.day-bars\s*\{[^}]*margin:\s*0 0 16px/);
});
