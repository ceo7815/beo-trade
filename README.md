# Beo-Trade

Paper-only autonomous options desk. Live trading is rejected at startup.

Server deployment is one command after secrets are filled on the server:

```bash
git clone https://github.com/ceo7815/beo-trade.git
cd beo-trade
cp .env.example .env
./scripts/deploy.sh
```

Read `README_DEPLOY.md` before the first boot. Do not commit `.env`.

## SPY Intraday / מומנטום תוך-יומי

A second paper engine, separate from the options desk. It trades SPY only, on its own Alpaca paper account, in its own process (`spy` in docker-compose) and its own SQLite file (`data/spy.sqlite`). A crash there does not stop options orders. It is off until `SPY_ENGINE_ENABLED=true`.

Rules follow spec v5 (Zarattini, Aziz & Barbon 2024), not the paper where they differ:

- Noise zone: 14-day average absolute move from the open at that minute. Upper = max(open, prior close) × (1+σ). Lower = min(open, prior close) × (1−σ).
- Decisions only at HH:00 and HH:30, 10:00–15:30 America/New_York. The trail (max of the upper band and VWAP for a long) is checked at those times only.
- Flat two minutes before the close (15:58 on a regular day, or two minutes before an early close). No overnight hold.
- Size is min(1, 0.02 / 14-day daily volatility) of **live equity**, in whole shares, and never more than 1× equity. Nothing is hardcoded to a dollar balance.
- Live book is long-only (`SPY_ALLOW_SHORT=false`). Short math exists and is tested; it does not send orders unless that flag is turned on.
- The live book is `SPY_TRADE_SYMBOLS` (default `SPY,QQQ,IWM`), up to `SPY_MAX_POSITIONS` (3). Each name has its own noise zone. Size is `min(1, 0.02/σ) × equity / positions`, and the book never exceeds 1× equity or available buying power. `SPY_SHADOW_SYMBOLS` defaults to empty; those names are logged and never ordered.
- Brakes: PDT guard stays on. At or above $25,000 it does not block. Below that line a missing day trade is `PDT_LIMIT` and is not retried. Daily loss is 2% of total equity. Each symbol may enter twice a day, and the book stops at `SPY_MAX_ENTRIES_DAY` (6). A 10% drawdown and the kill switch halt the engine.

Set `SPY_ALPACA_ACCOUNT` to the new $25,000 paper account when its number is known. An empty pin does not guess an old account. If the pin is set and the broker account differs, the engine sends nothing. Keys are `SPY_ALPACA_KEY_ID` and `SPY_ALPACA_SECRET_KEY` on `https://paper-api.alpaca.markets` only.

Enable on XCloud: set those variables in the server env, set `SPY_ENGINE_ENABLED=true`, then Deploy Now. A git push does not start it. Kill switch: `touch` the data volume file `spy_kill.flag`, or set `SPY_KILL_SWITCH=true`.

The deleted view accounts stay in the account menu as unavailable: `PA3LU0NE5` (beo 10K ILS) and `PA3YUE43D` (SPY Intraday 20K ILS). Their credentials are not used, and a failure there does not change options trading.

## Options 2 / אופציות 2

A third paper book. It trades the 26.10–30.10.2026 rules from commit `b7f62cf` (the $3,500 kill from `d86be52` and pacing from `f172fa4` are included). Percent rules are unchanged. Dollar brakes are that book's dollars × 0.40 for a $40,000 account: daily kill $1,400 (or a 5% loss, whichever is first) and the day-floor ladder `1,600 → 800` through `6,000 → 4,400`. The profile file is `backend/app/config/trading.toml`'s sibling `trading_v2610_40k.toml`. `TRADING_CONFIG_PATH` cannot select it. The process loads it only when `DESK_ID=options2` and `OPTIONS2_PROFILE=v2610_40k`.

It does not change the main options book (PA3N1EPU6, 20K ₪) or Eli's intraday book. Own compose services `options2-api` and `options2-worker`, own volume `options2_data`, own lock, own SQLite. It never reads `ALPACA_API_KEY`. Keys are `OPTIONS2_ALPACA_API_KEY` and `OPTIONS2_ALPACA_API_SECRET`. `OPTIONS2_ACCOUNT_LAST4` must be the suffix of that paper account. A live Alpaca host is refused and no order is sent. The engine is off until `OPTIONS2_ENABLED=true`. While it is off the worker heartbeats and does not scan.

Not in this book: the CALL block before 10:00 New York, the 0.50–8 premium band, the marketable stop, the extended-CALL filter, the day trail, and the 30.10 bug-lot exclusion.

Avi's QQQ debit-spread service stays in compose and stays off (`AVI_ENGINE_ENABLED=false`). Its page is an archive at `/avi`. The switcher shows אופציות, מסחר יומי אלי, and אופציות 2.

Enable on XCloud: set the three Options 2 values in the server env, set `OPTIONS2_ENABLED=true`, then Deploy Now. A git push does not start it. The same read key used by the other desks can read `/api/v1/system`, `/api/v1/report/trades`, `/api/v1/risk`, `/api/v1/trading-rules`, `/api/v1/decisions`, and `/api/v1/broker/alpaca/positions` on the Options 2 API.

Read API, same key as the other desk reads: `/api/v1/spy/status`, `/api/v1/spy/trades`, `/api/v1/spy/daily`, `/api/v1/spy/signals`. The site switcher at the top moves between Options and SPY Intraday.
