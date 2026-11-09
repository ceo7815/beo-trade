-- Read-only archive of the retired Paper 100K account.
-- These tables are append/update only. No cleanup job deletes them.

create table public.archive_paper_100k_runs (
  id text primary key,
  started_at timestamptz not null default now(),
  finished_at timestamptz,
  status text not null default 'running',
  detail text not null default '',
  counts jsonb not null default '{}'::jsonb
);

create table public.archive_paper_100k_account (
  id text primary key,
  equity text not null default '',
  cash text not null default '',
  last_equity text not null default '',
  account_last4 text not null default '',
  payload jsonb not null default '{}'::jsonb,
  archived_at timestamptz not null default now()
);

create table public.archive_paper_100k_orders (
  alpaca_order_id text primary key,
  status text not null default '',
  symbol text not null default '',
  submitted_at text not null default '',
  payload jsonb not null default '{}'::jsonb,
  archived_at timestamptz not null default now()
);

create index ix_archive_paper_100k_orders_status on public.archive_paper_100k_orders (status);
create index ix_archive_paper_100k_orders_submitted_at on public.archive_paper_100k_orders (submitted_at);

create table public.archive_paper_100k_activities (
  activity_id text primary key,
  activity_type text not null default '',
  payload jsonb not null default '{}'::jsonb,
  archived_at timestamptz not null default now()
);

create index ix_archive_paper_100k_activities_type on public.archive_paper_100k_activities (activity_type);

create table public.archive_paper_100k_equity (
  id text primary key,
  period text not null,
  timeframe text not null,
  ts bigint not null,
  equity text not null default '',
  profit_loss text not null default '',
  profit_loss_pct text not null default '',
  archived_at timestamptz not null default now(),
  constraint uq_archive_100k_equity unique (period, timeframe, ts)
);

create index ix_archive_paper_100k_equity_period on public.archive_paper_100k_equity (period);
create index ix_archive_paper_100k_equity_ts on public.archive_paper_100k_equity (ts);

create table public.archive_paper_100k_positions (
  id text primary key,
  run_id text not null,
  symbol text not null default '',
  payload jsonb not null default '{}'::jsonb,
  archived_at timestamptz not null default now()
);

create index ix_archive_paper_100k_positions_run on public.archive_paper_100k_positions (run_id);

create table public.archive_paper_100k_local (
  id text primary key,
  source text not null,
  source_id text not null,
  payload jsonb not null default '{}'::jsonb,
  archived_at timestamptz not null default now(),
  constraint uq_archive_100k_local unique (source, source_id)
);

create index ix_archive_paper_100k_local_source on public.archive_paper_100k_local (source);

alter table public.archive_paper_100k_runs enable row level security;
alter table public.archive_paper_100k_account enable row level security;
alter table public.archive_paper_100k_orders enable row level security;
alter table public.archive_paper_100k_activities enable row level security;
alter table public.archive_paper_100k_equity enable row level security;
alter table public.archive_paper_100k_positions enable row level security;
alter table public.archive_paper_100k_local enable row level security;
