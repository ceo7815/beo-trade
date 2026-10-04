#!/usr/bin/env bash
# Check the server env file. Never print secret values.
set -euo pipefail

ENV_FILE="${1:-.env}"
if [[ ! -f "$ENV_FILE" ]]; then
  echo "INVALID: env file is missing"
  exit 1
fi

value_of() {
  local key="$1"
  local line
  line="$(grep -E "^${key}=" "$ENV_FILE" | tail -n 1 || true)"
  printf '%s' "${line#*=}"
}

fail() {
  echo "INVALID: $1"
  exit 1
}

mode="$(value_of TRADING_MODE)"
paper="$(value_of PAPER_ONLY)"
app_env="$(value_of APP_ENV)"
base="$(value_of ALPACA_BASE_URL)"
model="$(value_of OPENAI_MODEL)"

[[ "$mode" == "PAPER" ]] || fail "TRADING_MODE must be PAPER"
[[ "$paper" == "true" ]] || fail "PAPER_ONLY must be true"
[[ "$app_env" == "local" ]] || fail "APP_ENV must stay local; production mode refuses the SQLite runtime"
if [[ -n "$base" && "$base" != "https://paper-api.alpaca.markets" ]]; then
  fail "ALPACA_BASE_URL must be empty or the Alpaca paper host"
fi
if [[ -n "$model" && "$model" != "gpt-6.1-sol" ]]; then
  fail "OPENAI_MODEL must be gpt-6.1-sol"
fi

if grep -E '^[^#]*https?://api\.alpaca\.markets' "$ENV_FILE" | grep -v 'paper-api.alpaca.markets' | grep -q .; then
  fail "a live Alpaca host is present"
fi

while IFS= read -r line || [[ -n "$line" ]]; do
  [[ "$line" =~ ^[A-Za-z_][A-Za-z0-9_]*= ]] || continue
  key="${line%%=*}"
  val="${line#*=}"
  low="$(printf '%s' "$val" | tr '[:upper:]' '[:lower:]')"
  case "$low" in
    *changeme*|*replace-me*|*replace_me*|*your-key*|*your_key*|*todo*|*password*|*secret-here*)
      fail "placeholder value in ${key}"
      ;;
  esac
done < "$ENV_FILE"

echo "ENV VALID"
echo "TRADING_MODE=PAPER"
echo "PAPER_ONLY=true"
echo "APP_ENV=local"
echo "LIVE=DISABLED"
