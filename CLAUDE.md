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
- `.github/workflows/dump.yml`: Tuesdays 12:00 UTC, Parquet backup of every table into `data/` (commits to the repo).
- Storage is Supabase project `RynoQB12_Model` (free tier, RLS on, service key only in Actions). Schema: `sql/001_schema.sql`
  (10 tables + `v_line_gaps`), `sql/002_bets.sql` (bets). Both already applied.
- ntfy.sh topic (secret) receives alerts; Ryan replies on the same topic to log a bet ("Seahawks -1.5 $5").
  `logger/bets.py` parses it, matches the game, links the alert it followed, stores a `bets` row, confirms with "Logged".
- Local dev: `DRY_RUN=1 ODDS_FIXTURE=tests/fixtures/odds_sample.json ESPN_FIXTURE=tests/fixtures/espn_sample.json
  NTFY_TOPIC=t NTFY_FIXTURE=tests/fixtures/ntfy_sample.json python -m logger.run_poll`; tests: `DRY_RUN=1 pytest -q` (11 pass).

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
- Credit burn with real 10-minute polling: ~17/day (news pulls hit the 6/day cap every day, plus closes/pres/scheduled).
  431 remaining on Oct 13. That is ~510/month, slightly over the 500 tier, and config.py says the key is shared with a
  pick'em (~180/month). ESPN shows 8-13 "triggers" on nearly every poll, which looks like noise; worth inspecting the
  `injury_items` diffs before touching NEWS_DAILY_CAP (cooldowns are frozen by the hard rules).
- Backtest (private repo): no edge vs the closing spread with public features, faint totals signal, model shrinks itself
  to ~50%. So the model does NOT yet put a number in alerts; the alerts are MyBookie-vs-consensus gaps only.

## Next work, in the order Claude recommended and Ryan has not yet confirmed

1. ~~Fix the scheduling problem~~ done 2026-10-12. Next: check the bets table for Oct 11-12 duplicates; inspect why
   ESPN triggers fire every poll (credit burn).
2. Grading + CLV: ESPN scoreboard scores -> grade each `bets` row (won/lost/push, units), CLV vs MyBookie and consensus
   close; alert follow-up (did MyBookie move toward consensus before kickoff?).
3. Weekly report: Tuesday markdown committed to the repo + short ntfy summary (record, units, CLV, alert stats, credits).
4. Player adjustments table (only if Ryan has real opinions to enter), tracked and graded as a hypothesis.
5. Model number in alerts / suggestion log: not yet, model has nothing to say at the 55% threshold.
6. Step 3: one-time $30 Odds API history month to test Monday openers vs close (Ryan OK'd the idea earlier).
7. Cosmetic: repo description typo "consnsus".

## Working style Ryan asked for

Be objective, push back with evidence, ask clarifying questions, check and recheck, no assumptions. Info over UI.
He bets $5 and accepts MyBookie's "chasing steam" risk (rule 33) at that stake.
