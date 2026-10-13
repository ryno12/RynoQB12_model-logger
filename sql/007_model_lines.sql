-- Model lines: the private model repo publishes its fair spread and total for the coming week (rqb/live.py, Tue/Fri);
-- the logger shows them in alerts and grades them against the closing line and MyBookie's close.
-- Sign convention (nflverse): fair_spread is the expected HOME margin, positive = home favored.

create table if not exists model_lines (
  game_key        text primary key,            -- '<season>-<week>-<AWAY>-<HOME>' (nflverse abbreviations)
  season          int not null,
  week            int not null,
  home_team       text not null,               -- full names, as in games
  away_team       text not null,
  gameday         date,
  computed_at     timestamptz not null,
  model_version   text,
  fair_spread     numeric,                     -- expected home margin
  fair_total      numeric,
  shrink_spread   numeric,                     -- 0..1: how much of (fair - line) has historically been real
  sigma_spread    numeric,                     -- residual spread after shrinking, points
  shrink_total    numeric,
  sigma_total     numeric,
  nfl_spread      numeric,                     -- nflverse pre-game line, for the record
  nfl_total       numeric,
  inputs          jsonb,
  -- filled by the logger's grading when the game is final
  close_spread    numeric,                     -- consensus close as a home margin (minus the home point)
  close_total     numeric,
  mb_close_spread numeric,
  mb_close_total  numeric,
  result_margin   numeric,
  result_total    numeric,
  spread_pick     text,                        -- home / away: the side the fair line prefers at the consensus close
  spread_vs_close text,                        -- won / lost / push
  spread_pick_mb  text,
  spread_vs_mb    text,
  total_pick      text,                        -- over / under
  total_vs_close  text,
  total_pick_mb   text,
  total_vs_mb     text,
  graded_at       timestamptz
);
create index if not exists model_lines_week_idx on model_lines (season, week);

alter table alerts
  add column if not exists model_point numeric,    -- the model's fair point for the alerted side (spreads) or total
  add column if not exists model_edge  numeric,    -- shrunk edge at MyBookie's number, points, positive = model agrees
  add column if not exists model_p     numeric;    -- model probability the alerted side covers at MyBookie's number
