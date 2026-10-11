# RynoQB12_model — logger

Step 0 of the RynoQB12_model plan: record NFL odds, injury news and MyBookie-vs-consensus line gaps,
at $0 ongoing cost, starting now so the history exists before the model does.

What it does, every 10 minutes from GitHub Actions:

1. Polls ESPN's injury list (free, no API credits). Stores every item and every status change.
2. Decides whether to spend 2 Odds API credits on a pull of NFL spreads + totals (MyBookie, DraftKings, FanDuel, BetMGM, BetRivers):
   - **close** — a tracked game kicks off within 40 minutes and has no closing pull yet
   - **pre** — a tracked game kicks off in 90–130 minutes (the inactives window) and has no pre pull yet. Keyed to each
     game's own kickoff, so London 9:30am ET games, Thursday, Monday, Melbourne or any other odd slot is covered.
   - **scheduled** — Monday 13:00 UTC (lines open) and Friday 22:00 UTC (status reports)
   - **news** — an injury event met the trigger rules, and the cooldown, daily cap and credit reserve allow it
3. If it pulled: upserts `games`, writes a snapshot row **only when a book's line or price changed**, updates `odds_latest`,
   computes MyBookie vs the comparison-book median, and sends a push alert for any gap of 1+ point, any key-number
   (3 or 7) crossing, or 10+ cents of price at the same line. Repeats are suppressed for 3 hours unless the gap grows.
4. Writes a `runs` row with credits used and remaining, so the budget is always visible.

Weekly (Tuesdays): dumps every table to Parquet under `data/dumps/<date>/` and commits it, which is the backup
and also keeps the Supabase free project from pausing.

**What it never does:** place a bet, or read MyBookie's website. MyBookie quotes come only from The Odds API.
MyBookie's house rules prohibit bots and scraping; automated betting would put the account at risk.

## Setup (about 30 minutes)

### 1. Supabase
1. Create a free project named `RynoQB12_model` (your account can hold two free projects; the pick'em uses one).
2. SQL editor → paste `sql/001_schema.sql` → Run. It creates all tables, the 2026 season row and a `v_line_gaps` view.
3. Settings → API: copy the **Project URL** and the **service_role** key (or a new `sb_secret_...` key). Server-side only; never put it in client code.

### 2. ntfy (phone alerts)
1. Install the ntfy app (iOS or Android), tap +, subscribe to a topic. Use something unguessable, e.g. `rynoqb12-granite-4471`. Topics on ntfy.sh are public by name.
2. Test from a terminal: `curl -d "hello from the logger" ntfy.sh/YOUR-TOPIC` — the phone should buzz.

### 3. GitHub
1. Create a **public** repo `RynoQB12_model-logger` on ryno12 (public so Actions minutes are unlimited; nothing sensitive lives here).
2. Push this folder to it.
3. Settings → Secrets and variables → Actions → New repository secret, four times:
   `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `ODDS_API_KEY`, `NTFY_TOPIC`.
4. Actions tab → enable workflows if prompted → open **Poll injuries and odds** → Run workflow with `force_pull = 1`.
   That first run costs 2 credits, seeds every table, and sends alerts for any current gaps. Check the run log and the
   `runs` table in Supabase. From then on it runs itself every 10 minutes.

### Odds API key
The free tier is 500 credits a month per account. The pick'em pulls DraftKings spreads every 4 hours (1 credit each,
about 180 a month). This logger budgets roughly 200–250: scheduled pulls are about 16 a month, pre and close pulls about
one each per distinct kickoff slot (roughly 50 a week on a full slate with international games), news pulls are capped
at 6 a day and 45 minutes apart, and it reads the `x-requests-remaining` header on every call and stops making news
pulls below 60 remaining and scheduled pulls below 20. If it ever gets tight, set `ODDS_EVERY_HOURS: '6'` in the pick'em's
`sync.yml` to free about 60 credits. Closing pulls are never skipped for budget reasons.

## Running locally (no accounts needed)

```bash
pip install -r requirements.txt pytest
pytest -q                                    # logic checks on fixtures
DRY_RUN=1 ODDS_FIXTURE=tests/fixtures/odds_sample.json ESPN_FIXTURE=tests/fixtures/espn_sample.json FORCE_PULL=1 \
  python -m logger.run_poll                  # full run with local JSON state under .state/, alerts printed not sent
```

With real keys in `.env` (`set -a; source .env; set +a`), `FORCE_PULL=1 python -m logger.run_poll` does a real pull from a Mac.

## Tables (Supabase)

| Table | What |
|---|---|
| `games` | One row per Odds API event: season, week, kickoff, teams, `pre_pull_at`, `close_pull_at` |
| `odds_latest` | Current line and price per (event, book, market, outcome) |
| `odds_snapshots` | Change-only history; `is_first` marks the first observed quote (not necessarily the opener) |
| `injury_items` | Current state of every ESPN injury item; status `Cleared` when it drops off the list |
| `injury_events` | Append-only: new items, status changes, clears; `triggered_pull` says whether it caused an odds pull |
| `alerts` | Every alert sent, with the MyBookie and consensus numbers at the time |
| `runs` | One row per poller run: reason, credits used/remaining, counts, notes |
| `sync_state` | Scheduled-window bookkeeping, news cooldown and daily count, last known credits, ntfy read position |
| `bets` | Bets Ryan posts to the ntfy topic, parsed and matched to a game and (when it followed one) the alert |
| `v_line_gaps` | View: MyBookie vs comparison median for every open market, for quick looks in the SQL editor |

Closing line per game and book = the last `odds_snapshots` row (or `odds_latest`) before `games.commence_time`.
The `close` pull makes sure one exists within 40 minutes of kickoff; the `pre` pull captures the inactives window.
International games need nothing special here: their odd kickoff times drive both windows automatically.

## Logging a bet (reply on the ntfy topic)

Post a plain note to the same ntfy topic the alerts arrive on. Every poll reads new notes (free), parses them, matches
the game, stores a `bets` row and pushes a one-line "Logged" confirmation back. Examples that work:

- `Seahawks -1.5 $5` or `I took Seattle -1.5 vs Denver next week on Thurs` (spread: team and a signed number)
- `Over 44.5 Bears Falcons -115 $10` (total: over/under, number, the teams; price and stake optional)
- `A17 5` (alert number 17 at its line, $5) — the number is in the alert text once the model is live
- Defaults when not stated: price -110, stake $5. Two team names must both be in the game. A spread number must be signed.

Anything it cannot place is saved with status `needs_review` and a "Could not log" push tells you why; it is retried
every run, so a bet on a game not yet in the odds table gets matched when the game appears. Nothing is lost.
Run `sql/002_bets.sql` once in the Supabase SQL editor to create the table. `NTFY_FIRST_SINCE` (24h) sets how far back the
first run reads; ntfy.sh keeps messages for about 12 hours, so a note older than that is gone unless you resend it.

## Trigger rules (injury events that spend credits)

- Any player: a new item with status **Out** or **Doubtful**, or an existing item changing to one of those.
- Quarterbacks: any status change on a known item; a new item at **Questionable** or worse; dropping off the list after being Out or Doubtful.
- Never: `Active` practice notes, Questionable for non-QBs, and the very first poll (cold start sees ~800 "new" items).

## Alert thresholds (frozen until the midseason checkpoint)

- Spread: MyBookie gives a side 1+ point more than the comparison median, or the two numbers straddle 3 or 7.
- Total: MyBookie's number is 1+ point below consensus (Over) or above (Under).
- Price: same number, MyBookie at least 10 cents better.
- Needs at least 2 comparison books quoting the market. Caesars is paid-only on The Odds API and is not used.

## Known limits

- GitHub cron can run 10–30 minutes late under load. The `runs` table shows actual run times; if alerts are consistently
  late versus MyBookie's moves, an always-on machine is the next step. Not before.
- ESPN's injuries endpoint is undocumented. When it changes, `logger/espn.py` is the only file to fix.
- GitHub disables scheduled workflows after 60 days without repository activity. The weekly dump commits keep it alive.
- The first captured line is the first line *we* saw, not the opener. Monday 13:00 UTC is close to the opener for most games.
- Timestamps are stored in UTC; alerts show Mountain time.

## Config knobs (environment variables, all optional)

`ALERT_GAP_POINTS` (1.0), `ALERT_GAP_CENTS` (10), `ALERT_COOLDOWN_HOURS` (3), `NEWS_COOLDOWN_MIN` (45), `NEWS_DAILY_CAP` (6),
`CREDIT_RESERVE_NEWS` (60), `CREDIT_RESERVE_SCHED` (20), `CLOSE_WINDOW_MIN` (40), `PRE_WINDOW_MIN_LO` (90), `PRE_WINDOW_MIN_HI` (130),
`CONSENSUS_BOOKS`, `MIN_CONSENSUS_BOOKS` (2), `NTFY_FIRST_SINCE` (24h), `DEFAULT_STAKE` (5), `DEFAULT_PRICE` (-110).
