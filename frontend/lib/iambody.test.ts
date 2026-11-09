import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { homeSpring, stagger } from "./iambodyMotion.ts";

test("the desk keeps the spring and the dark gold glass skin", () => {
  assert.equal(homeSpring.stiffness, 260);
  assert.equal(homeSpring.damping, 20);
  assert.equal(stagger, 0.09);
  const css = readFileSync(new URL("../app/iambody.css", import.meta.url), "utf8");
  assert.match(css, /#e7c98a/);
  assert.match(css, /#f3d7a4/);
  assert.match(css, /rgba\(10, 12, 16/);
  assert.match(css, /scale\(0\.97\)/);
  assert.doesNotMatch(css, /#f4f4f2/);
});

test("desk charts are recharts area and bar, not a hand-drawn polyline", () => {
  const source = readFileSync(new URL("../components/charts.tsx", import.meta.url), "utf8");
  assert.match(source, /from "recharts"/);
  assert.match(source, /animationDuration=\{900\}/);
  assert.match(source, /<Area /);
  assert.match(source, /<Bar /);
  assert.doesNotMatch(source, /<polyline/);
});
