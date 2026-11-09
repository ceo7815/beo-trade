create table public.shadow_rule_eval (
  id text primary key,
  recommendation_id text not null default '',
  order_id text not null default '',
  evaluated_at timestamptz not null,
  outcome text not null default '',
  existing_block_reason text not null default '',
  underlying text not null default '',
  option_symbol text not null default '',
  side text not null default '',
  dte integer,
  minutes_since_open numeric(18, 6),
  underlying_price numeric(18, 6),
  session_open numeric(18, 6),
  prior_close numeric(18, 6),
  vwap numeric(18, 6),
  price_vs_open_pct numeric(18, 6),
  price_vs_vwap_pct numeric(18, 6),
  change_vs_prior_close_pct numeric(18, 6),
  rules json not null,
  context json not null default '{}',
  created_at timestamptz not null default now()
);

create index ix_shadow_rule_eval_evaluated_at on public.shadow_rule_eval (evaluated_at);
create index ix_shadow_rule_eval_recommendation on public.shadow_rule_eval (recommendation_id);

alter table public.shadow_rule_eval enable row level security;
