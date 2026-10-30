create table public.trade_exclusions (
  id text primary key,
  symbol text not null,
  quantity integer not null,
  entry_price numeric(18, 6) not null,
  entry_after timestamptz,
  entry_before timestamptz,
  reason text not null default '',
  active boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index ix_trade_exclusions_symbol on public.trade_exclusions (symbol);

alter table public.trade_exclusions enable row level security;
