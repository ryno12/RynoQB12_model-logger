-- 2026-11-02: Supabase security advisor (25 Oct) flagged rls_disabled_in_public. model_lines (007) and
-- model_lines_history (008) were created without row-level security, and 001 never enabled it in code. With RLS on
-- and no policies, the public anon/authenticated roles can read and write nothing; the logger and the model repo use
-- the service-role key, which bypasses RLS, so nothing of ours changes. Idempotent.
alter table seasons             enable row level security;
alter table games               enable row level security;
alter table odds_latest         enable row level security;
alter table odds_snapshots      enable row level security;
alter table injury_items        enable row level security;
alter table injury_events       enable row level security;
alter table alerts              enable row level security;
alter table runs                enable row level security;
alter table sync_state          enable row level security;
alter table heartbeat           enable row level security;
alter table bets                enable row level security;
alter table model_lines         enable row level security;
alter table model_lines_history enable row level security;
-- a plain view runs as its owner and would bypass RLS on the tables under it; make it respect the caller's rights
alter view v_line_gaps set (security_invoker = true);

-- every table in public, for the run log: anything still without RLS shows here as 'OFF'
select c.relname as table_name, case when c.relrowsecurity then 'on' else 'OFF' end as rls
  from pg_class c join pg_namespace n on n.oid = c.relnamespace
 where n.nspname = 'public' and c.relkind = 'r'
 order by 2, 1;
