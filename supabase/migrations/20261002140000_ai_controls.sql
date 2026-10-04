create table public.ai_fingerprint_locks (
  fingerprint text primary key,
  claimed_at timestamptz not null
);

create table public.ai_request_logs (
  id uuid primary key default gen_random_uuid(),
  request_id text,
  scan_id text,
  candidate_id text,
  candidate_fingerprint text,
  model text,
  input_tokens integer,
  cached_tokens integer,
  output_tokens integer,
  reasoning_tokens integer,
  total_tokens integer,
  latency_ms integer,
  estimated_cost numeric(18, 6),
  cache_hit boolean,
  decision text,
  retry_count integer,
  created_at timestamptz not null
);

alter table public.ai_fingerprint_locks enable row level security;
alter table public.ai_request_logs enable row level security;
