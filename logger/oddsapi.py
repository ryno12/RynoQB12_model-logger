"""The Odds API v4 client. One call = spreads + totals for the US region = 2 credits."""
import json

import requests

from . import config


class OddsResult:
    def __init__(self, events, used, remaining):
        self.events = events
        self.credits_used = used
        self.credits_remaining = remaining


def fetch_odds() -> OddsResult:
    if config.ODDS_FIXTURE:
        with open(config.ODDS_FIXTURE) as f:
            data = json.load(f)
        events = data["events"] if isinstance(data, dict) and "events" in data else data
        return OddsResult(events, None, None)
    if not config.ODDS_API_KEY:
        raise RuntimeError("ODDS_API_KEY is required (or set ODDS_FIXTURE for testing)")
    url = f"https://api.the-odds-api.com/v4/sports/{config.SPORT}/odds"
    params = {
        "apiKey": config.ODDS_API_KEY,
        "regions": config.REGIONS,
        "markets": config.MARKETS,
        "bookmakers": ",".join(config.BOOKS),
        "oddsFormat": "american",
        "dateFormat": "iso",
    }
    r = requests.get(url, params=params, timeout=60)
    if r.status_code != 200:
        raise RuntimeError(f"Odds API {r.status_code}: {r.text[:300]}")

    def _int(h):
        v = r.headers.get(h)
        try:
            return int(float(v)) if v is not None else None
        except ValueError:
            return None

    return OddsResult(r.json(), _int("x-requests-used"), _int("x-requests-remaining"))


def flatten(events):
    """Yield one quote dict per (event, bookmaker, market, outcome)."""
    for ev in events:
        for bk in ev.get("bookmakers", []):
            for mk in bk.get("markets", []):
                if mk.get("key") not in ("spreads", "totals"):
                    continue
                for oc in mk.get("outcomes", []):
                    yield {
                        "event_id": ev["id"],
                        "bookmaker": bk["key"],
                        "market": mk["key"],
                        "outcome": oc["name"],
                        "point": oc.get("point"),
                        "price": oc.get("price"),
                        "book_update": mk.get("last_update") or bk.get("last_update"),
                    }
