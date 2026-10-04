-- Beo-Trade initial schema.
-- Apply on Supabase with the SQL editor or the Supabase CLI.
-- The backend connects with the database role, which bypasses RLS.
-- Browser clients using the publishable key can read BUY rows only.

create extension if not exists vector;
create extension if not exists pgcrypto;

create or replace function public.set_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

create table public.users (
  id uuid primary key references auth.users (id) on delete cascade,
  email text not null default '',
  paper_cash numeric(18, 6) not null,
  paper_starting_cash numeric(18, 6) not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table public.underlyings (
  id uuid primary key default gen_random_uuid(),
  symbol text not null unique,
  name text not null default '',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table public.option_contracts (
  id uuid primary key default gen_random_uuid(),
  underlying_id uuid not null references public.underlyings (id),
  option_symbol text not null unique,
  call_put text not null check (call_put in ('CALL', 'PUT')),
  strike numeric(18, 6) not null,
  expiration date not null,
  multiplier integer not null default 100,
  created_at timestamptz not null default now()
);

create table public.option_quotes (
  id uuid primary key default gen_random_uuid(),
  contract_id uuid not null references public.option_contracts (id),
  bid numeric(18, 6) not null,
  ask numeric(18, 6) not null,
  last numeric(18, 6) not null,
  bid_size integer not null default 0,
  ask_size integer not null default 0,
  observed_at timestamptz not null,
  created_at timestamptz not null default now()
);
create index ix_option_quotes_contract_time on public.option_quotes (contract_id, observed_at desc);

create table public.option_trades (
  id uuid primary key default gen_random_uuid(),
  contract_id uuid not null references public.option_contracts (id),
  price numeric(18, 6) not null,
  size integer not null,
  observed_at timestamptz not null,
  created_at timestamptz not null default now()
);

create table public.option_greeks (
  id uuid primary key default gen_random_uuid(),
  contract_id uuid not null references public.option_contracts (id),
  delta numeric(18, 6) not null,
  gamma numeric(18, 6) not null,
  theta numeric(18, 6) not null,
  vega numeric(18, 6) not null,
  implied_volatility numeric(18, 6) not null,
  observed_at timestamptz not null,
  created_at timestamptz not null default now()
);
create index ix_option_greeks_contract_time on public.option_greeks (contract_id, observed_at desc);

create table public.option_snapshots (
  id uuid primary key default gen_random_uuid(),
  contract_id uuid not null references public.option_contracts (id),
  payload jsonb not null,
  observed_at timestamptz not null,
  created_at timestamptz not null default now()
);

create table public.market_snapshots (
  id uuid primary key default gen_random_uuid(),
  underlying_id uuid not null references public.underlyings (id),
  price numeric(18, 6) not null,
  open numeric(18, 6) not null,
  high numeric(18, 6) not null,
  low numeric(18, 6) not null,
  close numeric(18, 6) not null,
  volume bigint not null,
  relative_volume numeric(18, 6) not null,
  observed_at timestamptz not null,
  created_at timestamptz not null default now()
);
create index ix_market_snapshots_underlying_time on public.market_snapshots (underlying_id, observed_at desc);

create table public.volatility_snapshots (
  id uuid primary key default gen_random_uuid(),
  underlying_id uuid not null references public.underlyings (id),
  implied_volatility numeric(18, 6) not null,
  historical_volatility numeric(18, 6),
  iv_rank numeric(18, 6),
  iv_percentile numeric(18, 6),
  observed_at timestamptz not null,
  created_at timestamptz not null default now()
);

create table public.news_articles (
  id uuid primary key default gen_random_uuid(),
  source text not null,
  url text not null unique,
  headline text not null,
  published_at timestamptz not null,
  symbols jsonb not null,
  category text not null default '',
  event_type text not null default '',
  importance integer not null default 0,
  content text not null default '',
  source_credibility numeric(8, 4) not null default 0,
  ai_summary text,
  sentiment text,
  observed_at timestamptz not null,
  created_at timestamptz not null default now()
);

create table public.news_events (
  id uuid primary key default gen_random_uuid(),
  article_id uuid not null references public.news_articles (id),
  event_type text not null,
  symbols jsonb not null,
  importance integer not null default 0,
  observed_at timestamptz not null,
  created_at timestamptz not null default now()
);

create table public.news_embeddings (
  id uuid primary key default gen_random_uuid(),
  article_id uuid not null unique references public.news_articles (id),
  embedding vector(1536),
  created_at timestamptz not null default now()
);

create table public.candidates (
  id uuid primary key default gen_random_uuid(),
  scan_id uuid not null,
  underlying text not null,
  option_symbol text not null default '',
  score numeric(18, 6) not null default 0,
  status text not null,
  reasons jsonb not null default '{}'::jsonb,
  observed_at timestamptz not null,
  created_at timestamptz not null default now()
);

create table public.ai_analyses (
  id uuid primary key default gen_random_uuid(),
  prompt_version text not null,
  model text not null,
  input_hash text not null,
  output jsonb not null,
  decision text not null check (decision in ('BUY', 'SUPPRESS')),
  snapshot jsonb not null,
  cost numeric(18, 6) not null,
  input_tokens integer not null default 0,
  cached_tokens integer not null default 0,
  output_tokens integer not null default 0,
  created_at timestamptz not null default now()
);

create table public.recommendations (
  id uuid primary key,
  analysis_id uuid references public.ai_analyses (id),
  user_id uuid references public.users (id),
  decision text not null check (decision in ('BUY', 'SUPPRESS')),
  underlying text not null,
  underlying_price numeric(18, 6) not null,
  option_symbol text not null,
  call_put text not null check (call_put in ('CALL', 'PUT')),
  strike numeric(18, 6) not null,
  expiration date not null,
  option_price numeric(18, 6) not null,
  bid numeric(18, 6) not null,
  ask numeric(18, 6) not null,
  delta numeric(18, 6) not null,
  gamma numeric(18, 6) not null,
  theta numeric(18, 6) not null,
  vega numeric(18, 6) not null,
  iv numeric(18, 6) not null,
  volume integer not null,
  open_interest integer not null,
  max_entry_price numeric(18, 6) not null,
  quantity integer not null,
  holding_window_min integer not null,
  holding_window_max integer not null,
  thesis text not null default '',
  catalyst text not null default '',
  risk text not null default '',
  invalidation text not null default '',
  reason_codes jsonb not null,
  gate_results jsonb not null,
  suppress_reason text not null default '',
  scenarios jsonb not null,
  quote_observed_at timestamptz not null,
  created_at timestamptz not null default now()
);
create index ix_recommendations_decision_time on public.recommendations (decision, created_at desc);

create table public.paper_trades (
  id uuid primary key,
  user_id uuid not null references public.users (id),
  recommendation_id uuid not null references public.recommendations (id),
  option_symbol text not null,
  quantity integer not null,
  entry_price numeric(18, 6) not null,
  entry_at timestamptz not null,
  exit_price numeric(18, 6),
  exit_at timestamptz,
  exit_reason text not null default '',
  pnl_dollars numeric(18, 6),
  pnl_percent numeric(18, 6),
  max_favorable numeric(18, 6) not null default 0,
  max_adverse numeric(18, 6) not null default 0,
  bid numeric(18, 6) not null,
  ask numeric(18, 6) not null,
  iv numeric(18, 6) not null,
  delta numeric(18, 6) not null,
  gamma numeric(18, 6) not null,
  theta numeric(18, 6) not null,
  vega numeric(18, 6) not null,
  underlying_at_entry numeric(18, 6) not null,
  created_at timestamptz not null default now()
);
create index ix_paper_trades_user on public.paper_trades (user_id);

create table public.paper_positions (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.users (id),
  trade_id uuid not null references public.paper_trades (id),
  option_symbol text not null,
  quantity integer not null,
  status text not null check (status in ('open', 'closed')),
  opened_at timestamptz not null,
  closed_at timestamptz
);
create index ix_paper_positions_user on public.paper_positions (user_id, status);

create table public.trade_outcomes (
  id uuid primary key default gen_random_uuid(),
  trade_id uuid not null references public.paper_trades (id),
  horizon_minutes integer not null,
  return_percent numeric(18, 6) not null,
  max_gain_percent numeric(18, 6) not null,
  max_loss_percent numeric(18, 6) not null,
  time_to_peak_seconds integer not null,
  time_to_drawdown_seconds integer not null,
  success boolean not null,
  rule text not null,
  observed_at timestamptz not null,
  created_at timestamptz not null default now()
);

create table public.backtest_runs (
  id uuid primary key default gen_random_uuid(),
  status text not null,
  params jsonb not null,
  leakage_checks jsonb not null,
  started_at timestamptz not null,
  finished_at timestamptz,
  created_at timestamptz not null default now()
);

create table public.backtest_results (
  id uuid primary key default gen_random_uuid(),
  run_id uuid not null references public.backtest_runs (id),
  payload jsonb not null,
  created_at timestamptz not null default now()
);

create table public.ai_usage (
  id uuid primary key default gen_random_uuid(),
  process text not null,
  model text not null,
  input_tokens integer not null,
  cached_tokens integer not null,
  output_tokens integer not null,
  cost numeric(18, 6) not null,
  created_at timestamptz not null
);
create index ix_ai_usage_created on public.ai_usage (created_at desc);

create table public.audit_logs (
  id uuid primary key default gen_random_uuid(),
  actor text not null,
  action text not null,
  entity text not null,
  entity_id text not null,
  payload jsonb not null,
  created_at timestamptz not null default now()
);

create table public.system_events (
  id uuid primary key default gen_random_uuid(),
  level text not null,
  event_type text not null,
  message text not null,
  payload jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create table public.alerts (
  id uuid primary key default gen_random_uuid(),
  user_id uuid references public.users (id),
  recommendation_id uuid references public.recommendations (id),
  kind text not null,
  message text not null,
  created_at timestamptz not null default now(),
  read_at timestamptz
);

create trigger users_set_updated_at
before update on public.users
for each row execute function public.set_updated_at();

create trigger underlyings_set_updated_at
before update on public.underlyings
for each row execute function public.set_updated_at();

create table public.watchlist (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.users (id),
  symbol text not null,
  created_at timestamptz not null default now(),
  unique (user_id, symbol)
);
create index ix_watchlist_user on public.watchlist (user_id);

alter table public.users enable row level security;
alter table public.underlyings enable row level security;
alter table public.option_contracts enable row level security;
alter table public.option_quotes enable row level security;
alter table public.option_trades enable row level security;
alter table public.option_greeks enable row level security;
alter table public.option_snapshots enable row level security;
alter table public.market_snapshots enable row level security;
alter table public.volatility_snapshots enable row level security;
alter table public.news_articles enable row level security;
alter table public.news_events enable row level security;
alter table public.news_embeddings enable row level security;
alter table public.candidates enable row level security;
alter table public.ai_analyses enable row level security;
alter table public.recommendations enable row level security;
alter table public.paper_trades enable row level security;
alter table public.paper_positions enable row level security;
alter table public.trade_outcomes enable row level security;
alter table public.backtest_runs enable row level security;
alter table public.backtest_results enable row level security;
alter table public.ai_usage enable row level security;
alter table public.audit_logs enable row level security;
alter table public.system_events enable row level security;
alter table public.alerts enable row level security;
alter table public.watchlist enable row level security;

revoke all on all tables in schema public from anon, authenticated;

grant select on public.recommendations to authenticated;
grant select on public.paper_trades to authenticated;
grant select on public.paper_positions to authenticated;
grant select on public.trade_outcomes to authenticated;
grant select on public.alerts to authenticated;
grant select, insert, delete on public.watchlist to authenticated;

create policy recommendations_buy_read
on public.recommendations
for select
to authenticated
using (decision = 'BUY');

create policy paper_trades_own_read
on public.paper_trades
for select
to authenticated
using ((select auth.uid()) = user_id);

create policy paper_positions_own_read
on public.paper_positions
for select
to authenticated
using ((select auth.uid()) = user_id);

create policy trade_outcomes_own_read
on public.trade_outcomes
for select
to authenticated
using (
  exists (
    select 1 from public.paper_trades
    where paper_trades.id = trade_outcomes.trade_id
      and paper_trades.user_id = (select auth.uid())
  )
);

create policy watchlist_own_read
on public.watchlist
for select
to authenticated
using ((select auth.uid()) = user_id);

create policy watchlist_own_insert
on public.watchlist
for insert
to authenticated
with check ((select auth.uid()) = user_id);

create policy watchlist_own_delete
on public.watchlist
for delete
to authenticated
using ((select auth.uid()) = user_id);

create policy alerts_buy_read
on public.alerts
for select
to authenticated
using (kind = 'BUY' and (user_id is null or user_id = (select auth.uid())));

do $$
begin
  if exists (select 1 from pg_publication where pubname = 'supabase_realtime') then
    alter publication supabase_realtime add table public.recommendations;
  end if;
end $$;
