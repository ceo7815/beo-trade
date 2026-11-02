import assert from "node:assert/strict";
import test from "node:test";
import { withTradingPeriod } from "./account.ts";

test("withTradingPeriod appends to risk and finance", () => {
  assert.equal(withTradingPeriod("/api/v1/risk", "paper_100k"), "/api/v1/risk?trading_period=paper_100k");
  assert.equal(
    withTradingPeriod("/api/v1/report/trades?period=today", "paper_10k_ils"),
    "/api/v1/report/trades?period=today&trading_period=paper_10k_ils",
  );
});

test("withTradingPeriod skips unrelated paths", () => {
  assert.equal(withTradingPeriod("/api/v1/system", "paper_100k"), "/api/v1/system");
});
