---
name: beo-trade-desk
description: >-
  Operate the Beo-Trade paper options desk. Use when changing scans, entries,
  exits, risk, positions, the dashboard, ThetaData, Alpaca paper, OpenAI
  decisions, or when the user asks what the system did, why a trade won or
  lost, or whether a number is real.
---

# Beo-Trade desk

Private single-user PAPER options desk. Hebrew RTL UI at https://beo-trade.1wp.site. The user deploys with Deploy Now on XCloud. A git push does not change the live site.

Answer the user in Hebrew. Status words: VERIFIED, IMPLEMENTED, NOT VERIFIED, BLOCKED. A number is VERIFIED only after a live read in this turn. Do not invent quotes, fills, P&L, news, or trades. Do not print secrets, account ids, or account numbers.

## What this desk trades

Buy one option and sell that same option. Intents: `buy_to_open`, `sell_to_close`. No short options, covered calls, spreads, stock, crypto, or a second broker.

Market data is ThetaData. Execution is Alpaca paper (`https://paper-api.alpaca.markets`). `TRADING_MODE` stays PAPER. Never use the live Alpaca host or send a real-money order.

Do not install the `thetadata` package. Do not upgrade httpx. No Redis. Runtime DB is SQLite on the `beo_data` volume. Do not run `docker compose down -v`. Do not POST `/integrations/thetadata/test` or `/v3/terminal/shutdown`. GitHub is `github.com/ceo7815/beo-trade` only.

## Exits, in this order

KILL_SWITCH, SESSION_CLOSE, EXPIRATION, INVALIDATION, LIQUIDITY, STOP or PROTECTED_STOP, TRAILING, TIME_STOP.

Do not widen the stop. One full stop is about 40% of the premium (`initial_stop_decline_pct` 0.40). Protection arms at +1R and moves the stop to entry minus 0.25R. The trail arms at +1.5R and exits 15% off the peak. 0DTE holding limit is 90 minutes. 1DTE is 180. Winners came from the trail and the time stop on liquid names (HOOD, MU, MSTR, SMCI).

A stop is not a guaranteed price, and nothing protects a position held overnight. A resting sell above the bid is moved to the bid after `exit_reprice_seconds`, and on every check inside `exit_reprice_close_minutes` of the close. Before that fix a 15:40 exit limit of 2.34 sat unfilled, expired at the close, and QQQ PUT sold at 0.30 the next open (−$4,620).

## Liquidity

Entry only: option volume at least 50 and open interest at least 100 (`trading.toml`). Daily volume starts at zero, so those floors must not force an exit.

A liquidity exit fires only after the bid-ask spread stays wider than 25% for two monitor checks (`liquidity_exit_confirmations`). One wide quote is not an exit. AGQ and KORU lost about $2,030 because a thin contract was sold into the first wide spread.

`max_chains_per_scan` is 12. `max_buys_per_scan` is 2. Up to 10 positions, one per underlying. Do not raise size to get more trades.

## Decisions

A paper buy may happen when the quant gates pass and the model is silent (`allow_quant_entry`). Do not require OpenAI or Benzinga for an entry. MU and SMCI were profitable without an AI review. If OpenAI returns 429, say whether it is `insufficient_quota` or `rate_limit_exceeded` once the scan records it. Do not paste the API key.

Size and the equity check must use the same Alpaca account snapshot. Reading equity twice while a position is open blocks every new buy with `EQUITY_SOURCE_MISMATCH`.

## When a scan looks stuck

The monitor heartbeat can be fresh while the scan loop is inside one stage. Read `scan_progress` on `/backend/api/v1/desk`: `stage_done` and `theta_requests`. The slow stage has been minute-bar history from ThetaData. Do not call the terminal down from a missing quote alone.

## External options skills

Do not install marketplace options skills into this repo. They teach short premium, Yahoo Finance, Deribit, or Interactive Brokers. Those sources and those structures are not this desk.
