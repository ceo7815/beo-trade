import assert from "node:assert/strict";
import { describe, test } from "node:test";

import { holdScreen, unavailable } from "./holdScreen.ts";

describe("failed reads do not replace a good screen", () => {
  test("an unavailable payload is recognized and held back", () => {
    assert.equal(unavailable({ available: false, closed: [] }), true);
    assert.equal(unavailable({ available: true, closed: [{ symbol: "QQQ" }] }), false);

    const current = { available: true, summary: { total_pnl: "10" }, closed: [{ symbol: "QQQ" }] };
    const held = holdScreen(current, { available: false, detail: "down" });
    assert.equal(held?.available, true);
    assert.equal(held?.stale, true);
    assert.equal(held?.closed[0].symbol, "QQQ");
    assert.equal(unavailable(held), false);
  });

  test("the first failure is kept when there is nothing good yet", () => {
    assert.equal(holdScreen(null, { available: false }), null);
  });

  test("a good read replaces the previous screen", () => {
    const next = holdScreen({ available: true, stale: true, closed: [1] }, { available: true, closed: [1, 2] });
    assert.deepEqual(next, { available: true, closed: [1, 2] });
  });
});
