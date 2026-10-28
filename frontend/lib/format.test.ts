import assert from "node:assert/strict";
import test from "node:test";
import { parseStamp, sessionStamp } from "./format.ts";

test("naive audit stamps are UTC and show Dubai with New York", () => {
  const parsed = parseStamp("2026-10-28T06:49:00");
  assert.equal(parsed?.toISOString(), "2026-10-28T06:49:00.000Z");
  const legacy = parseStamp("2026-10-28 06:49:00.000000");
  assert.equal(legacy?.toISOString(), "2026-10-28T06:49:00.000Z");
  assert.equal(sessionStamp("2026-10-28T06:49:00+00:00"), "10:49 (02:49 ET)");
});
