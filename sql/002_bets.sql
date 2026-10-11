-- Bets Ryan posts to the ntfy topic, parsed and matched to games. Run once in the Supabase SQL editor.
create table if not exists bets (
  id              bigserial primary key,
  ntfy_id         text unique,
  received_at     timestamptz not null,
  raw_text        text not null,
  event_id        text references games(id),
  matchup         text,
  kickoff         timestamptz,
  market          text,                  -- spreads | totals
  side            text,                  -- team name, or Over / Under
  line            numeric,
  price           int,
  stake           numeric,
  alert_id        bigint,                -- the alert it followed, if any
  tag             text,                  -- steam (followed an alert) | own
  status          text not null,         -- open | needs_review | won | lost | push | void
  result_units    numeric,
  settled_at      timestamptz,
  note            text,
  run_id          bigint
);
create index if not exists bets_event_idx on bets (event_id);
alter table bets enable row level security;
