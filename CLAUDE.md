# RynoQB12_model-logger — project memory for Claude Code

Public repo. The always-running half of Ryan's NFL spreads-and-totals betting project (RynoQB12). It logs odds,
injury news and Ryan's own bets, and pushes line alerts to his phone. The model, backtest and hypothesis register
live in the private sibling repo `~/Repos/RynoQB12_model` (read its CLAUDE.md too). The full plan is
`NFL_Betting_Model_Consolidated_Plan_v3.docx` (Ryan has it; ask if you need a section).

## Hard rules (do not relax without Ryan saying so in the session)

- Never put secrets in chat, commits, or files: the Supabase service key, the Odds API key and the ntfy topic live only in
  GitHub Actions secrets (`SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `ODDS_API_KEY`, `NTFY_TOPIC`) and in Ryan's local
  `.env` (gitignored). Tests and dry runs use `DRY_RUN=1` with fixtures under `tests/fixtures/`.
- Never scrape MyBookie, log into it, or place bets. MyBookie prices come only from The Odds API (`mybookieag`).
  MyBookie fraud rule 7 forbids bots on their site; we read a third-party feed instead.
- Odds API free tier: 500 credits/month. A pull of spreads+totals for one region costs 2 credits. Reserves and cooldowns
  in `logger/config.py` are deliberate; do not add pulls without checking `runs.credits_remaining`.
- Alert thresholds and bankroll rules are frozen until the midseason checkpoint (after 100 logged suggestions).
  Bankroll: $300, $5 flat, max 6 bets / $30 a week, 1 spread + 1 total per game, pause at $180 or negative CLV after 75 graded.
- Git identity: commits and pushes are Ryan's personal GitHub `ryno12` (remote already set to
  `https://ryno12@github.com/...`). Never use a work account. The gh CLI holds both accounts; it defaults to the
  work one. Run `gh auth switch -u ryno12` before any gh write to this repo (dispatch, PR) and switch back after.

## How it runs

- `.github/workflows/poll.yml`: self-chaining loop. Each run polls every 10 minutes for 5h45m (`python -m logger.run_poll`
  per poll), then dispatches its successor with the built-in token (`actions: write`). The `*/10` cron is only a backstop;
  GitHub throttled it to ~1 run per 5 hours, and its queued runs show as "cancelled" when the successor replaces them.
  A manual run with `force_pull=1` polls once in its own concurrency group. Each poll: ESPN injuries (free)
  -> ntfy bet capture (free) -> decide on an odds pull (close / pre / scheduled / news / manual) -> snapshots, gaps, alerts.
- `.github/workflows/dump.yml`: Tuesdays 12:00 UTC, Parquet backup of every table into `data/` (commits to the repo),
  then `logger/report.py` builds `reports/<season>/week-NN.md` for the week that just ended from that dump and pushes a
  one-line ntfy summary (bets record/$/CLV, season + bankroll + pause rules, alert record and moved-to-close share,
  credits/polls/failures). One report per week: it skips if `reports/<season>/week-NN.md` exists (`--force` to redo),
  because the Tuesday cron fires hours late and can follow a manual dispatch (Oct 13: two reports, two "Could not
  log" replies because bet capture did not know the report title; both fixed). Local: `python -m logger.report --dump
  data/dumps/<date> --no-push --week 5`. Any new kind of push needs its title in `bets.is_system`, and must keep a
  tag from `bets.SYSTEM_TAGS` (since Oct 19 any push with one is skipped; the model push had been logged as 3 bets,
  removed by sql/009).
  Dispatching dump.yml by hand (as ryno12) is the way to read any table from this Mac, which has no Supabase credentials.
- `.github/workflows/migrate.yml`: `workflow_dispatch` with a `file` input under `sql/`; applies it with psql in one
  transaction and records `migration:<file>` in `sync_state`. Two ways in, either secret works: `SUPABASE_ACCESS_TOKEN`
  (a Supabase personal access token; runs the file through the Management API database/query endpoint, no database
  password needed) or `SUPABASE_DB_URL` (Session-pooler URI; direct connections are IPv6-only) plus optional
  `SUPABASE_DB_PASSWORD`. Working since Oct 13 via the URI + `SUPABASE_DB_PASSWORD` (Ryan pasted the whole URI into
  that secret; the workflow takes the password out of it). The log prints the URI's shape with password and project
  ref masked. Commit the SQL first, then dispatch; the run log says "applied <file>". 003 and 004 applied this way.
- Storage is Supabase project `RynoQB12_Model` (free tier, RLS on, service key only in Actions). Schema: `sql/001_schema.sql`
  (10 tables + `v_line_gaps`), `sql/002_bets.sql` (bets), `sql/003_grading.sql` (score, profit, CLV and alert
  follow-up columns), `sql/004_void_inplay_alerts.sql` (data fix), all applied (003 and 004 via migrate.yml, 2026-10-13).
- Grading (`logger/grading.py`, free, every poll): games 3.5h past kickoff with no score -> ESPN scoreboard by Eastern
  date (`logger/scores.py`) -> `games.home_score/away_score/espn_status/final_at`; open bets on those games -> won/lost/
  push/void, `profit` ($), `result_units` (profit / UNIT_DOLLARS=5); closing quotes = last pre-kickoff `odds_snapshots`
  row per book (`close_seen_at` says how close to kickoff); CLV vs consensus close (`clv_points`, `clv_cents`) and vs
  MyBookie close (`clv_mb_*`); each alert graded as a $5 bet at its line (`alerts.result/profit`) with
  `close_gap_points` and `moved_toward_consensus`; alerts sent at or after kickoff are voided (in-play lines).
  Sign conventions are in the module docstring. Games with no final
  after 7 days are marked `unresolved` and left alone, so apply 003 within a week of the games you want graded.
- ntfy.sh topic (secret) receives alerts; Ryan replies on the same topic to log a bet ("Seahawks -1.5 $5", or "A12" /
  "A12 10" to take alert 12 at its line for $5 / $10). Each push is titled "A<id> Line gap: ..." (row inserted before the
  push, since Oct 13). Its "Checked because:" line is the reason for the odds PULL (an injury change, a pre-kickoff or
  closing check, a scheduled check), not for that game; one pull covers the whole board (`alerts.why_checked`). `logger/bets.py` parses it, matches the game, links the alert, stores a `bets` row, confirms "Logged".
  Events whose kickoff has passed are dropped right after the odds pull (in-play lines never snapshot or alert).
- Local dev: `DRY_RUN=1 ODDS_FIXTURE=tests/fixtures/odds_sample.json ESPN_FIXTURE=tests/fixtures/espn_sample.json
  NTFY_TOPIC=t NTFY_FIXTURE=tests/fixtures/ntfy_sample.json python -m logger.run_poll`; tests: `DRY_RUN=1 pytest -q` (22 pass).
  The odds fixture's kickoffs are Oct 10-12 2026, so a dry run now reports "3 in-play events ignored" and pulls nothing;
  copy the fixture and shift `commence_time` into the future to exercise snapshots and alerts.
  No `python` on this Mac: use `uv run --python 3.12 --with pytest --with 'requests>=2.31' python -m pytest -q`.

## Model lines (live since 2026-10-13, unproven)

The private model repo publishes its fair spread (home margin, nflverse sign) and total for the next two weeks to
`model_lines` (`rqb/live.py`, workflow `model_lines.yml`: Mon 12:00, Tue 14:00 and Fri 22:30 UTC = 6am, 8am, 4:30pm
MDT, needs the two Supabase secrets on that repo; two weeks since 2026-10-14 because books post next week's lines
Sunday night and the logger already alerts on them). Each row carries `shrink_*` (0..1, how much of the model's disagreement with a line has historically
been real; v1 had spreads ~0.00, totals ~0.07; v2-market since Oct 26 has spreads ~0.16, totals ~0.35) and `sigma_*`.
Since 2026-10-26 the model is v2-market: fair = the live consensus line (read from `odds_latest`) + the model's
predicted adjustment, so its numbers sit within a point or two of the market (see the model repo's CLAUDE.md). `logger/model.py` turns a row into the model's
view of any side at any number (edge = shrink * raw, p = Phi(edge / sigma)); every alert ends with that line, labelled
unproven, and `alerts.model_point/model_edge/model_p` are stored. Grading fills the row's close (consensus and
MyBookie), result and pick outcomes (`spread_vs_close`, `spread_vs_mb`, `total_vs_*`); the weekly report has a Model
section. After each model run the poller pushes one "Model vs MyBookie" summary (biggest raw leans vs MyBookie's
current numbers; wording is Ryan's "option 2" of 2026-10-14, see `model.disagreements`; trust shown once in the
footer; `sync_state model:notified` keeps it to once per run). Every run is also kept
in `model_lines_history` (sql/008) so Tuesday and Friday numbers can be compared. The model gets no say in bets until
it clears the hypothesis bar in the model repo.

## Scheduling (fixed 2026-10-12)

GitHub ran the `*/10` cron 8 times in 44 hours. Fixed with the self-chaining loop above (commit a4e7bc8); verified
Oct 13: run 1 did 36 polls and handed off, run 2 the same, run 3 started on time. If the chain ever dies (dispatch
failure), the cron backstop restarts it within a few hours; or click "Run workflow" with force_pull=0.
Fallbacks if GitHub ever blocks the chain: Supabase pg_cron + pg_net dispatch with a fine-grained token, or an always-on
machine (Ryan is willing, but only if needed).

## State as of 2026-10-13 (Week 6)

- Live since Oct 10 afternoon: 26 events, 472 quotes, 5 alerts on the first pull; credits remaining 473.
- Ryan's first logged bet: "I took Seattle -1.5 vs Denver next week on Thurs" (Thu Oct 15 SEA@DEN, followed the SEA -1.5
  alert, tag steam). Price assumed -110, stake $5, unless he corrects it.
- Bet capture crashed on Oct 11 (KeyError 'id' in match_alert, then a duplicate ntfy_id insert). Fixed in 99309f3. The
  Oct 12 17:01 UTC run (old code) logged "4 bets"; Ryan has not yet confirmed whether those are real or re-logs, so
  check the `bets` table for duplicates before grading.
- Credit burn Oct 10-13 was ~17/day (431 left on Oct 13), driven by bogus injury triggers: Supabase caps every
  response at 1000 rows whatever `limit=` says, `injury_items` had 1650, and the 650 unloaded rows looked new on every
  poll (11 "Out" triggers a poll, news pulls at the 6/day cap). Fixed Oct 13 with paged `SupabaseStore.select_all`.
  `injury_events` holds ~9000 junk rows from Oct 11-13; harmless, but ignore them in any trigger analysis.
  The Odds API key IS shared with the pick'em (confirmed Oct 13: its sync.yml pulls odds every 4 hours, 1 credit each,
  and our runs log shows 1-credit drops between our own pulls, 8 in 3 days). Nominal pick'em use is 180/month; actual
  is ~3/day because GitHub throttles its hourly cron too. Budget for us: ~320/month, i.e. ~10/day.
- Backtest (private repo): no edge vs the closing spread with public features, faint totals signal, model shrinks itself
  to ~50%. So the model does NOT yet put a number in alerts; the alerts are MyBookie-vs-consensus gaps only.

## Next work, in the order Claude recommended and Ryan has not yet confirmed

1. ~~Scheduling~~ done 2026-10-12. ~~Injury trigger noise~~ done 2026-10-13. Bets table checked Oct 13: no duplicates,
   but the Seattle -1.5 bet was lost (expired from ntfy before the fix) and needs re-posting ("Seahawks -1.5 (-110) $5 A1").
   "Balt -4.5 (-110)" (row 4) is a real Week 6 bet (Ravens @ Browns, Sun Oct 18); it sat in needs_review because "balt"
   was not an alias (added Oct 13; the retry pass matched it). Duplicates and tests deleted Oct 13 (005).
   Bet messages must arrive before kickoff; the matcher only looks at upcoming games.
2. ~~Grading + CLV~~ built and live 2026-10-13 (see Grading above); 003 applied the same day, so the first poll after
   18:09 UTC graded Week 5: 14 finals, BUF +3.5 won (+$4.35, CLV 0), 35 alerts. 19 of those alerts were in-play
   lines (pre-fix) and are voided by `sql/004_void_inplay_alerts.sql`; the 16 real pre-game alerts went 7-9 (-$10.83),
   8 of 16 moved toward consensus by close. The BAL -4.5 bet is Week 6. Bets table cleaned Oct 13 via
   `sql/005_cleanup_bets.sql`: rows 4 (BAL -4.5), 5 (BUF +3.5, won) and 8 (SEA -1.5) remain. Ryan wants data fixes
   like that done through migrate.yml by Claude, not by hand in the SQL editor.
3. ~~Weekly report~~ built 2026-10-13 (see dump.yml above); first one is `reports/2026/week-05.md`.
4. Player adjustments table (only if Ryan has real opinions to enter), tracked and graded as a hypothesis.
5. ~~Model number in alerts~~ live 2026-10-13 (see Model lines). Next: a model-vs-MyBookie alert type once shrink
   is non-zero for anything, the simulation layer for alternate-line pricing, then player-level features as hypotheses.
6. ~~Step 3: $30 Odds API history month~~ done 2026-10-26 (key on the 20K plan until Ryan cancels; ~13.5k credits
   left after the download, so `runs.credits_remaining` reads high this month). Results in the model repo's
   `reports/openers.md`: MyBookie's better numbers win late in the week (Sunday-morning spreads 0.5+ gap 56%) and
   lose at Monday's open; evidence for the midseason threshold review.
7. ~~Cosmetic: repo description typo~~ fixed 2026-10-13 (both repos).

## Working style Ryan asked for

Times: Mountain time (America/Denver) everywhere Ryan reads them: chat, pushes, reports. He does not use UTC. Workflow
crons stay in UTC but say the MT equivalent in a comment (MDT = UTC-6 until Nov 1 2026, then MST = UTC-7).
Be objective, push back with evidence, ask clarifying questions, check and recheck, no assumptions. Info over UI.
He bets $5 and accepts MyBookie's "chasing steam" risk (rule 33) at that stake.
