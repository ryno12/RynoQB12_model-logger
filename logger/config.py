"""Configuration for the RynoQB12_model logger. Everything comes from environment variables
so the same code runs in GitHub Actions, on a Mac, or in dry-run mode with no accounts at all."""
import os
from datetime import date


def _env(name, default=None):
    v = os.environ.get(name)
    return v if v not in (None, "") else default


def _int(name, default):
    return int(_env(name, default))


def _float(name, default):
    return float(_env(name, default))


# --- modes -----------------------------------------------------------------
DRY_RUN = _env("DRY_RUN", "0") == "1"          # no Supabase, no ntfy; local JSON state in STATE_DIR
STATE_DIR = _env("STATE_DIR", ".state")           # dry-run state location
ODDS_FIXTURE = _env("ODDS_FIXTURE")               # path to a saved Odds API response (testing)
ESPN_FIXTURE = _env("ESPN_FIXTURE")               # path to a saved ESPN injuries response (testing)
FORCE_PULL = _env("FORCE_PULL", "0") == "1"       # manual run: pull odds regardless of schedule

# --- accounts --------------------------------------------------------------
SUPABASE_URL = (_env("SUPABASE_URL", "") or "").rstrip("/")
SUPABASE_SERVICE_ROLE_KEY = _env("SUPABASE_SERVICE_ROLE_KEY", "")
ODDS_API_KEY = _env("ODDS_API_KEY", "")
NTFY_TOPIC = _env("NTFY_TOPIC", "")
NTFY_SERVER = _env("NTFY_SERVER", "https://ntfy.sh")
NTFY_FIRST_SINCE = _env("NTFY_FIRST_SINCE", "24h")   # how far back to read the topic on the very first run
NTFY_FIXTURE = _env("NTFY_FIXTURE")                   # saved ntfy JSON messages (testing)
DEFAULT_STAKE = _float("DEFAULT_STAKE", 5.0)
DEFAULT_PRICE = _int("DEFAULT_PRICE", -110)

# --- odds -------------------------------------------------------------------
SPORT = "americanfootball_nfl"
REGIONS = "us"
MARKETS = "spreads,totals"                        # 2 credits per pull in one region
TARGET_BOOK = "mybookieag"
# Caesars (williamhill_us) is paid-only on The Odds API, so it is not in the free consensus.
CONSENSUS_BOOKS = [b.strip() for b in _env("CONSENSUS_BOOKS", "draftkings,fanduel,betmgm,betrivers").split(",")]
BOOKS = sorted(set([TARGET_BOOK] + CONSENSUS_BOOKS))
MIN_CONSENSUS_BOOKS = _int("MIN_CONSENSUS_BOOKS", 2)

# --- credit budget (free tier is 500/month, shared with the pick'em which uses ~180) -----
CREDIT_RESERVE_NEWS = _int("CREDIT_RESERVE_NEWS", 60)     # below this remaining, skip news-triggered pulls
CREDIT_RESERVE_SCHED = _int("CREDIT_RESERVE_SCHED", 20)   # below this, skip scheduled pulls too (closes still run)
NEWS_COOLDOWN_MIN = _int("NEWS_COOLDOWN_MIN", 45)         # minimum minutes between news-triggered pulls
NEWS_DAILY_CAP = _int("NEWS_DAILY_CAP", 6)                # max news-triggered pulls per UTC day
CLOSE_WINDOW_MIN = _int("CLOSE_WINDOW_MIN", 40)           # pull a close when a game starts within this many minutes
# Pre-kickoff pull: once per game when kickoff is 90-130 minutes away (inactives are posted 90 minutes before).
# Keyed to each game's own kickoff, so London 9:30am ET games, Thursday, Monday, Melbourne and any other odd slot
# are covered without a separate schedule. One pull covers every game in the window.
PRE_WINDOW_MIN_LO = _int("PRE_WINDOW_MIN_LO", 90)
PRE_WINDOW_MIN_HI = _int("PRE_WINDOW_MIN_HI", 130)

# --- scheduled pulls: (name, weekday 0=Mon..6=Sun, hour UTC, minute UTC) -------------------
# Evaluated by the 10-minute poller: the first run at or after the time on that weekday pulls once.
# 13:00 UTC = 7am MDT / 6am MST; 22:00 UTC = 4pm MDT / 3pm MST. Sunday morning is covered by the pre-kickoff pull.
SCHEDULED_PULLS = [
    ("monday_open", 0, 13, 0),
    ("friday_reports", 4, 22, 0),
]

# --- alert thresholds (frozen before go-live; revisit only at the midseason checkpoint) ------
ALERT_GAP_POINTS = _float("ALERT_GAP_POINTS", 1.0)
ALERT_GAP_CENTS = _int("ALERT_GAP_CENTS", 10)
KEY_NUMBERS = (3.0, 7.0)
ALERT_COOLDOWN_HOURS = _float("ALERT_COOLDOWN_HOURS", 3.0)   # same event+market+side not re-alerted within this window unless the gap grew

# --- injury triggers ----------------------------------------------------------
TRIGGER_STATUSES = {"Out", "Doubtful"}            # any position
TRIGGER_ANY_CHANGE_POSITIONS = {"QB"}             # QB: any status change triggers
ESPN_INJURIES_URL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/injuries"

# --- grading (free: ESPN scoreboard) ------------------------------------------------
ESPN_SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"
SCORES_FIXTURE = _env("SCORES_FIXTURE")           # saved scoreboard JSON (testing)
GRADE_AFTER_HOURS = _float("GRADE_AFTER_HOURS", 3.5)   # look for a final this long after kickoff
GRADE_GIVEUP_DAYS = _int("GRADE_GIVEUP_DAYS", 7)       # stop asking ESPN about a game after this; mark unresolved
UNIT_DOLLARS = _float("UNIT_DOLLARS", 5.0)             # result_units = profit / this (the flat stake)

# --- season ---------------------------------------------------------------------
# Fallback if the seasons table is unreachable. Week 1 Thursday 2026-09-10 -> Tuesday before is 09-08.
SEASONS_FALLBACK = {2026: date(2026, 9, 8)}
