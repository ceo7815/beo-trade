import assert from "node:assert/strict";
import test from "node:test";
import { SYSTEMS, parseStored, systemById, systemFromPath } from "./systems.ts";

test("routes pick the desk", () => {
  assert.equal(systemFromPath("/"), "options");
  assert.equal(systemFromPath("/positions"), "options");
  assert.equal(systemFromPath("/spy"), "eli");
  assert.equal(systemFromPath("/spy/anything"), "eli");
  assert.equal(systemFromPath("/o2"), "options2");
  assert.equal(systemFromPath("/o2/positions"), "options2");
  assert.equal(systemFromPath("/avi"), "avi");
});

test("the old spy desk key opens Eli and a saved Avi choice opens Options 2", () => {
  assert.equal(parseStored(null, "spy", null), "eli");
  assert.equal(parseStored("options", "spy", null), "options");
  assert.equal(parseStored("eli", null, "avi"), "options2");
  assert.equal(parseStored("avi", null, null), "options2");
  assert.equal(parseStored(null, null, "daily"), "eli");
  assert.equal(parseStored(null, null, null), null);
});

test("the switcher is options, Eli, and Options 2", () => {
  assert.deepEqual(SYSTEMS.map((row) => row.id), ["options", "eli", "options2"]);
  assert.equal(systemById("options2").label, "אופציות 2");
  assert.equal(systemById("options2").href, "/o2");
  assert.equal(systemById("avi").label, "ארכיון אבי");
  assert.equal(systemById("avi").href, "/avi");
  assert.equal(systemById("eli").statusPath, "/api/v1/spy/status");
  assert.equal(systemById("options").label, "אופציות");
});
