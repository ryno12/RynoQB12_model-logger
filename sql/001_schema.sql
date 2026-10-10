-- RynoQB12_model logger schema (Supabase / Postgres)
-- Paste into the Supabase SQL editor and run once.
-- All timestamps are stored in UTC (timestamptz). Display in America/Denver happens in reports.

create table if not exists seasons (
  year            int primary key,
  week1_tuesday   date not null          -- Tuesday before the Week 1 Thursday game
);
insert into seasons (year, week1_tuesday) values (2026, '2026-09-08')
on conflict (year) do nothing;

-- One row per Odds API event (game). Upserted on every odds pull.
create table if not exists games (
  id              text primary key,      -- Odds API event id
  season          int not null,
  week            int not null,
  commence_time   timestamptz not null,
  home_team       text not null,
  away_team       text not null,
  first_seen_at   timestamptz not null default now(),
  updated_at      timestamptz not null default now(),
  pre_pull_at     timestamptz,           -- the ~90-130 minute pre-kickoff pull (inactives window)
  close_pull_at   timestamptz            -- the <=40 minute pre-kickoff close pull
);
alter table games add column if not exists pre_pull_at timestamptz;
create index if not exists games_commence_idx on games (commence_time);

-- Current quote per (event, book, market, outcome). Upserted; used for change detection and consensus.
create table if not exists odds_latest (
  event_id        text not null references games(id),
  bookmaker       text not null,
  market          text not null,         -- spreads | totals
  outcome         text not null,         -- team name, or Over / Under
  point           numeric,
  price           int,                   -- American odds
  book_update     timestamptz,           -- bookmaker's last_update from the API
  seen_at         timestamptz not null,  -- our retrieval time
  primary key (event_id, bookmaker, market, outcome)
);

-- Append-only history: a row is written only when point or price changed since the last stored value.
create table if not exists odds_snapshots (
  id              bigserial primary key,
  event_id        text not null references games(id),
  bookmaker       text not null,
  market          text not null,
  outcome         text not null,
  point           numeric,
  price           int,
  book_update     timestamptz,
  seen_at         timestamptz not null,
  run_id          bigint,
  is_first        boolean not null default false   -- first observed quote for this key (not necessarily the opener)
);
create index if not exists odds_snapshots_event_idx on odds_snapshots (event_id, bookmaker, market);
create index if not exists odds_snapshots_seen_idx on odds_snapshots (seen_at);

-- Current state of every ESPN injury item we have seen.
create table if not exists injury_items (
  espn_item_id    text primary key,
  athlete_id      text,
  athlete_name    text,
  team            text,
  position        text,
  status          text,                  -- Out | Doubtful | Questionable | Active | Injured Reserve ...
  injury_type     text,                  -- e.g. Calf
  return_date     date,
  item_date       timestamptz,           -- ESPN's date on the item
  short_comment   text,
  long_comment    text,
  first_seen_at   timestamptz not null,
  last_seen_at    timestamptz not null
);
create index if not exists injury_items_team_idx on injury_items (team);

-- Append-only: every new item or status change, with what we saw and when.
create table if not exists injury_events (
  id              bigserial primary key,
  espn_item_id    text not null,
  athlete_id      text,
  athlete_name    text,
  team            text,
  position        text,
  old_status      text,
  new_status      text,
  item_date       timestamptz,
  short_comment   text,
  seen_at         timestamptz not null,
  triggered_pull  boolean not null default false,
  run_id          bigint
);
create index if not exists injury_events_seen_idx on injury_events (seen_at);

-- Alerts sent to the phone.
create table if not exists alerts (
  id              bigserial primary key,
  fingerprint     text not null,         -- event|market|side|mb_point|cons_point, used to avoid repeats
  event_id        text not null,
  market          text not null,
  side            text not null,         -- team name or Over/Under that is favorable at MyBookie
  mb_point        numeric,
  mb_price        int,
  cons_point      numeric,
  cons_price      int,
  cons_books      int,                   -- number of comparison books in the median
  gap_points      numeric,
  gap_cents       int,
  key_cross       boolean not null default false,
  reason          text,                  -- scheduled | close | news: <short comment>
  message         text not null,
  sent_at         timestamptz not null,
  run_id          bigint
);
create index if not exists alerts_event_idx on alerts (event_id, market);
create index if not exists alerts_sent_idx on alerts (sent_at);

-- One row per poller run.
create table if not exists runs (
  id              bigserial primary key,
  started_at      timestamptz not null default now(),
  finished_at     timestamptz,
  run_kind        text not null,         -- poll | manual
  pulled_odds     boolean not null default false,
  pull_reason     text,                  -- scheduled:monday | close | news | manual | none
  credits_used    int,                   -- x-requests-used header after the pull
  credits_remaining int,                 -- x-requests-remaining header after the pull
  events_seen     int,
  snapshot_rows   int,
  injury_events   int,
  alerts_sent     int,
  notes           text
);

-- Small key/value state (last scheduled pulls, cooldowns, daily trigger count).
create table if not exists sync_state (
  key             text primary key,
  value           text,
  updated_at      timestamptz not null default now()
);

-- Weekly heartbeat row keeps the free-tier project from pausing in the offseason.
create table if not exists heartbeat (
  id              bigserial primary key,
  at              timestamptz not null default now(),
  note            text
);

-- Handy view: latest MyBookie vs comparison-book median per event and market (spreads use the home outcome).
create or replace view v_line_gaps as
with cons as (
  select event_id, market, outcome,
         percentile_cont(0.5) within group (order by point) as cons_point,
         count(*) as cons_books
  from odds_latest
  where bookmaker in ('draftkings','fanduel','betmgm','betrivers')
  group by event_id, market, outcome
)
select g.season, g.week, g.commence_time, g.home_team, g.away_team,
       m.market, m.outcome, m.point as mb_point, m.price as mb_price,
       c.cons_point, c.cons_books, (m.point - c.cons_point) as gap_points, m.seen_at
from odds_latest m
join games g on g.id = m.event_id
join cons c on c.event_id = m.event_id and c.market = m.market and c.outcome = m.outcome
where m.bookmaker = 'mybookieag';
