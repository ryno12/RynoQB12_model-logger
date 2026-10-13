"""Final scores from the free ESPN scoreboard. One request per Eastern-time date; no credits involved."""
import json
from datetime import timedelta
from zoneinfo import ZoneInfo

import requests

from . import config
from .store import parse_ts

EASTERN = ZoneInfo("America/New_York")
FINAL = "STATUS_FINAL"
DEAD = {"STATUS_POSTPONED", "STATUS_CANCELED"}


def et_date(ts) -> str:
    """ESPN keys its scoreboard on the US-Eastern calendar date (a 00:20Z Monday kickoff is Sunday's slate)."""
    return parse_ts(ts).astimezone(EASTERN).strftime("%Y%m%d")


def fetch_scoreboard(date_yyyymmdd: str):
    if config.SCORES_FIXTURE:
        with open(config.SCORES_FIXTURE) as f:
            return json.load(f)
    r = requests.get(config.ESPN_SCOREBOARD_URL, params={"dates": date_yyyymmdd},
                     headers={"User-Agent": "curl/8.5.0", "Accept": "application/json"}, timeout=60)
    if r.status_code != 200:
        raise RuntimeError(f"ESPN scoreboard {r.status_code}: {r.text[:200]}")
    return r.json()


def normalize(payload):
    """One flat dict per game: teams by full display name (same names The Odds API uses), scores as ints."""
    out = []
    for e in payload.get("events", []):
        comp = (e.get("competitions") or [{}])[0]
        st = (comp.get("status") or {}).get("type") or {}
        teams = {}
        for c in comp.get("competitors", []):
            sc = c.get("score")
            teams[c.get("homeAway")] = ((c.get("team") or {}).get("displayName"), int(sc) if sc not in (None, "") else None)
        home, away = teams.get("home", (None, None)), teams.get("away", (None, None))
        out.append({"date": e.get("date"), "status": st.get("name"), "completed": bool(st.get("completed")),
                    "home_team": home[0], "away_team": away[0], "home_score": home[1], "away_score": away[1]})
    return out


def _norm(name):
    return (name or "").strip().lower()


def match_game(game, scoreboard_rows):
    """The scoreboard row for an Odds API game: same two teams, kickoff within 6 hours."""
    ct = parse_ts(game["commence_time"])
    for r in scoreboard_rows:
        if _norm(r["home_team"]) == _norm(game["home_team"]) and _norm(r["away_team"]) == _norm(game["away_team"]):
            rt = parse_ts(r["date"])
            if ct and rt and abs(rt - ct) <= timedelta(hours=6):
                return r
    return None
