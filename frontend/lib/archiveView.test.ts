import assert from "node:assert/strict";
import test from "node:test";
import { ARCHIVE_BADGE, archiveRangeLabel, dayDot, isArchivePeriod } from "./archiveView.ts";

test("archive range uses the New York session dates, not a UTC slice", () => {
  assert.equal(dayDot("2026-10-14"), "14.10");
  assert.equal(dayDot("2026-11-02T00:00:00+00:00"), "2.11");
  assert.equal(archiveRangeLabel("2026-10-07", "2026-11-02"), "7.10–2.11");
  assert.equal(isArchivePeriod("paper_100k"), true);
  assert.equal(isArchivePeriod("paper_20k_ils"), false);
  assert.equal(ARCHIVE_BADGE, "ארכיון · קריאה בלבד");
});
