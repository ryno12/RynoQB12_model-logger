-- Every model run, kept: model_lines holds only the latest number per game (that is what gets graded); this table
-- keeps each Tuesday / Friday / manual run so the Tuesday and Friday numbers can be compared and tracked separately.
create table if not exists model_lines_history (
  id              bigserial primary key,
  game_key        text not null,
  season          int not null,
  week            int not null,
  home_team       text not null,
  away_team       text not null,
  gameday         date,
  computed_at     timestamptz not null,
  model_version   text,
  fair_spread     numeric,
  fair_total      numeric,
  shrink_spread   numeric,
  sigma_spread    numeric,
  shrink_total    numeric,
  sigma_total     numeric,
  nfl_spread      numeric,
  nfl_total       numeric,
  inputs          jsonb,
  unique (game_key, computed_at)
);
create index if not exists model_lines_history_week_idx on model_lines_history (season, week);
