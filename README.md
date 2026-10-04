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
