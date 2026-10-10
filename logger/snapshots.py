"""Turn an Odds API response into games upserts, change-only snapshot rows and odds_latest upserts."""
from . import config
from .oddsapi import flatten
from .store import iso, now_utc, parse_ts
from .weeks import season_week


def _key(q):
    return (q["event_id"], q["bookmaker"], q["market"], q["outcome"])


def _num(x):
    return None if x is None else float(x)


def games_rows(events, seasons, existing_by_id):
    rows = []
    now = iso(now_utc())
    for ev in events:
        ct = parse_ts(ev["commence_time"])
        season, week = season_week(ct, seasons)
        prev = existing_by_id.get(ev["id"], {})
        rows.append({
            "id": ev["id"], "season": season, "week": week,
            "commence_time": ev["commence_time"],
            "home_team": ev["home_team"], "away_team": ev["away_team"],
            "first_seen_at": prev.get("first_seen_at", now),
            "updated_at": now,
            "pre_pull_at": prev.get("pre_pull_at"),
            "close_pull_at": prev.get("close_pull_at"),
        })
    return rows


def snapshot_rows(events, latest_rows, run_id):
    """Compare every quote with odds_latest. Returns (snapshots, latest_upserts)."""
    seen_at = iso(now_utc())
    latest = {_key(r): r for r in latest_rows}
    snaps, upserts = [], []
    for q in flatten(events):
        if q["bookmaker"] not in config.BOOKS:
            continue
        k = _key(q)
        prev = latest.get(k)
        changed = prev is None or _num(prev.get("point")) != _num(q["point"]) or (prev.get("price") is not None and int(prev["price"]) != int(q["price"])) or (prev.get("price") is None and q["price"] is not None)
        if changed:
            snaps.append({**q, "seen_at": seen_at, "run_id": run_id, "is_first": prev is None})
            upserts.append({**q, "seen_at": seen_at})
        else:
            # unchanged: refresh seen_at only (cheap, keeps 'last confirmed' time honest)
            upserts.append({**q, "seen_at": seen_at})
    return snaps, upserts
