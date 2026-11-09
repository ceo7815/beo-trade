import assert from "node:assert/strict";
import test from "node:test";
import { parseStored, systemById, systemFromPath } from "./systems.ts";

test("routes pick the desk", () => {
  assert.equal(systemFromPath("/"), "options");
  assert.equal(systemFromPath("/positions"), "options");
  assert.equal(systemFromPath("/spy"), "eli");
  assert.equal(systemFromPath("/spy/anything"), "eli");
  assert.equal(systemFromPath("/avi"), "avi");
});

test("the old spy desk key opens Eli and a query wins", () => {
  assert.equal(parseStored(null, "spy", null), "eli");
  assert.equal(parseStored("options", "spy", null), "options");
  assert.equal(parseStored("eli", null, "avi"), "avi");
  assert.equal(parseStored(null, null, "daily"), "eli");
  assert.equal(parseStored(null, null, null), null);
});

test("Avi options points at its own status route", () => {
  assert.equal(systemById("avi").label, "מסחר אופציות אבי");
  assert.equal(systemById("avi").statusPath, "/api/v1/avi/status");
  assert.equal(systemById("eli").statusPath, "/api/v1/spy/status");
  assert.equal(systemById("options").label, "אופציות");
});
