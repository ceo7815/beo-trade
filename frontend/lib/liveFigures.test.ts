import assert from "node:assert/strict";
import test from "node:test";
import { pickHeadline, streamApplies, streamUrl } from "./liveFigures.ts";

test("stream applies only to the live trading account", () => {
  assert.equal(streamApplies("current"), true);
  assert.equal(streamApplies(""), true);
  assert.equal(streamApplies("paper_100k"), false);
});

test("stream url keeps the trading period without dropping the proxy path", () => {
  assert.equal(streamUrl("current"), "/backend/api/v1/stream?trading_period=current");
  assert.equal(streamUrl("paper 100k"), "/backend/api/v1/stream?trading_period=paper%20100k");
});

test("a live tape replaces the 60s headline", () => {
  const head = pickHeadline({
    period: "current",
    transport: "sse",
    equity: "10250.15",
    displayPnl: "-270.00",
    polledEquity: "9000",
    polledPnl: "10",
  });
  assert.equal(head.live, true);
  assert.equal(head.equity, "10250.15");
  assert.equal(head.dayPnl, "-270.00");
});

test("a read-only period and a dead tape keep the polled figure", () => {
  const view = pickHeadline({
    period: "paper_100k",
    transport: "sse",
    equity: "100000",
    displayPnl: "5",
    polledEquity: "100000",
    polledPnl: "5",
  });
  assert.equal(view.live, false);
  assert.equal(view.dayPnl, "5");

  const stale = pickHeadline({
    period: "current",
    transport: "poll",
    equity: "1",
    displayPnl: "2",
    polledEquity: "3",
    polledPnl: "4",
  });
  assert.equal(stale.live, false);
  assert.equal(stale.equity, "3");
  assert.equal(stale.dayPnl, "4");
});
