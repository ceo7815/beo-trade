create table public.broker_accounts (
  id uuid primary key default gen_random_uuid(),
  environment text not null default 'PAPER',
  source text not null default 'alpaca',
  payload jsonb not null,
  configuration jsonb not null default '{}'::jsonb,
  observed_at timestamptz not null
);

create table public.broker_positions (
  id uuid primary key default gen_random_uuid(),
  environment text not null default 'PAPER',
  symbol text not null,
  payload jsonb not null,
  observed_at timestamptz not null
);

create table public.broker_order_snapshots (
  id uuid primary key default gen_random_uuid(),
  environment text not null default 'PAPER',
  broker_order_id text not null,
  client_order_id text not null default '',
  trade_id text not null default '',
  symbol text not null default '',
  internal_state text not null,
  payload jsonb not null,
  observed_at timestamptz not null
);

create table public.broker_fills (
  id uuid primary key default gen_random_uuid(),
  environment text not null default 'PAPER',
  execution_id text not null unique,
  trade_id text not null default '',
  broker_order_id text not null default '',
  symbol text not null default '',
  payload jsonb not null,
  observed_at timestamptz not null
);

create table public.broker_activities (
  id uuid primary key default gen_random_uuid(),
  environment text not null default 'PAPER',
  activity_id text not null unique,
  activity_type text not null default '',
  payload jsonb not null,
  observed_at timestamptz not null
);

create table public.broker_reconciliations (
  id uuid primary key default gen_random_uuid(),
  environment text not null default 'PAPER',
  ok boolean not null,
  mismatches jsonb not null,
  observed_at timestamptz not null
);

create table public.broker_trades (
  id uuid primary key,
  recommendation_id text not null default '',
  state text not null,
  source text not null,
  reason text not null default '',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table public.broker_trade_events (
  id uuid primary key default gen_random_uuid(),
  trade_id uuid not null references public.broker_trades(id),
  state text not null,
  source text not null,
  reason text not null default '',
  created_at timestamptz not null default now()
);

alter table public.broker_accounts enable row level security;
alter table public.broker_positions enable row level security;
alter table public.broker_order_snapshots enable row level security;
alter table public.broker_fills enable row level security;
alter table public.broker_activities enable row level security;
alter table public.broker_reconciliations enable row level security;
alter table public.broker_trades enable row level security;
alter table public.broker_trade_events enable row level security;

create table public.approvals (
  id uuid primary key default gen_random_uuid(),
  trade_id text not null,
  recommendation_id text not null unique,
  status text not null,
  approved_at timestamptz not null
);

alter table public.approvals enable row level security;
