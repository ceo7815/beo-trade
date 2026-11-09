import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { homeSpring, stagger } from "./iambodyMotion.ts";

test("the console uses the i-am-body spring and warm forest canvas", () => {
  assert.equal(homeSpring.stiffness, 260);
  assert.equal(homeSpring.damping, 20);
  assert.equal(stagger, 0.09);
  const css = readFileSync(new URL("../app/iambody.css", import.meta.url), "utf8");
  assert.match(css, /#f4f4f2/);
  assert.match(css, /#1d4333/);
  assert.match(css, /forest-wash/);
  assert.match(css, /scale\(0\.97\)/);
});

test("desk charts are recharts area and bar, not a hand-drawn polyline", () => {
  const source = readFileSync(new URL("../components/charts.tsx", import.meta.url), "utf8");
  assert.match(source, /from "recharts"/);
  assert.match(source, /animationDuration=\{900\}/);
  assert.match(source, /<Area /);
  assert.match(source, /<Bar /);
  assert.doesNotMatch(source, /<polyline/);
});
