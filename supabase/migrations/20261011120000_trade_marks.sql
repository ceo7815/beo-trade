create table public.trade_marks (
  id text primary key,
  symbol text not null,
  at timestamptz not null,
  bid numeric,
  ask numeric,
  underlying_price numeric,
  source text not null default 'monitor'
);

create index ix_trade_marks_symbol_at on public.trade_marks (symbol, at);

alter table public.trade_marks enable row level security;
