-- Grading and closing-line value. Apply in the Supabase SQL editor after 001 and 002.
-- Scores come from the free ESPN scoreboard; closing quotes are the last pre-kickoff odds_snapshots rows.

alter table games
  add column if not exists home_score   int,
  add column if not exists away_score   int,
  add column if not exists espn_status  text,          -- STATUS_FINAL | STATUS_POSTPONED | STATUS_CANCELED | unresolved
  add column if not exists final_at     timestamptz;   -- when we recorded the final score

alter table bets
  add column if not exists profit          numeric,      -- dollars: +4.55 on a $5 win at -110, -5 on a loss, 0 on a push
  add column if not exists close_seen_at   timestamptz,  -- time of the closing quote used (how close to kickoff it was)
  add column if not exists close_mb_point  numeric,
  add column if not exists close_mb_price  int,
  add column if not exists close_cons_point numeric,     -- median of CONSENSUS_BOOKS at close
  add column if not exists close_cons_price int,
  add column if not exists clv_points      numeric,      -- vs consensus close; positive = better line than close
  add column if not exists clv_cents       int,          -- vs consensus close, only when the points are equal
  add column if not exists clv_mb_points   numeric,      -- same, vs MyBookie's own close
  add column if not exists clv_mb_cents    int,
  add column if not exists graded_at       timestamptz;

alter table alerts
  add column if not exists close_seen_at    timestamptz,
  add column if not exists close_mb_point   numeric,
  add column if not exists close_mb_price   int,
  add column if not exists close_cons_point numeric,
  add column if not exists close_cons_price int,
  add column if not exists close_gap_points numeric,     -- the alert's gap recomputed at close (same sign convention)
  add column if not exists moved_toward_consensus boolean,  -- close gap smaller than the alert gap
  add column if not exists result          text,         -- won | lost | push | void, at the alerted MyBookie line
  add column if not exists profit          numeric,      -- $5 flat at mb_price
  add column if not exists graded_at       timestamptz;

create index if not exists bets_status_idx on bets (status);
create index if not exists alerts_graded_idx on alerts (graded_at);
