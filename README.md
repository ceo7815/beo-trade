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
- QQQ is a shadow (`SPY_SHADOW_SYMBOLS=QQQ`): the same signals and a hypothetical P&L are stored. No QQQ order is ever sent.
- Brakes: PDT guard (default on; under $25,000, no new entry when day trades left are 0, reason `PDT_LIMIT`; a PDT reject is not retried), 2% daily loss, 4 entries a day, 10% drawdown halt, and a kill switch.

Account pin for this deploy: `SPY_ALPACA_ACCOUNT=PA3YUE43D` (SPY Intraday 20K ILS). If the broker account number differs, the engine sends nothing. Keys are `SPY_ALPACA_KEY_ID` and `SPY_ALPACA_SECRET_KEY` on `https://paper-api.alpaca.markets` only.

Enable on XCloud: set those variables in the server env, set `SPY_ENGINE_ENABLED=true`, then Deploy Now. A git push does not start it. Kill switch: `touch` the data volume file `spy_kill.flag`, or set `SPY_KILL_SWITCH=true`.

The deleted options view account `PA3LU0NE5` (beo 10K ILS) stays in the history switcher as unavailable. Its credentials are not used, and a failure there does not change options trading.

Read API, same key as the other desk reads: `/api/v1/spy/status`, `/api/v1/spy/trades`, `/api/v1/spy/daily`, `/api/v1/spy/signals`. The site switcher at the top moves between Options and SPY Intraday.
