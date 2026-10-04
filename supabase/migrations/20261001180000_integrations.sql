create table public.integration_checks (
  id uuid primary key default gen_random_uuid(),
  integration_id text not null,
  status text not null,
  latency_ms integer,
  detail text not null default '',
  environment text not null default '',
  checked_at timestamptz not null default now()
);

create table public.integration_events (
  id uuid primary key default gen_random_uuid(),
  integration_id text not null,
  event text not null,
  detail text not null default '',
  created_at timestamptz not null default now()
);

create table public.broker_orders (
  id uuid primary key default gen_random_uuid(),
  broker text not null default 'alpaca',
  environment text not null default 'PAPER',
  broker_order_id text not null default '',
  client_order_id text not null default '',
  symbol text not null,
  side text not null,
  position_intent text not null,
  status text not null,
  recommendation_id text not null default '',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

alter table public.integration_checks enable row level security;
alter table public.integration_events enable row level security;
alter table public.broker_orders enable row level security;
