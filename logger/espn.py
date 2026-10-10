"""ESPN injuries endpoint (undocumented, free, no credits). Expect it to change; keep this module small."""
import json
import re

import requests

from . import config

_ID_RE = re.compile(r"/id/(\d+)/")


def fetch_injuries():
    if config.ESPN_FIXTURE:
        with open(config.ESPN_FIXTURE) as f:
            return json.load(f)
    r = requests.get(config.ESPN_INJURIES_URL, headers={"User-Agent": "curl/8.5.0", "Accept": "application/json"}, timeout=60)
    if r.status_code != 200:
        raise RuntimeError(f"ESPN {r.status_code}: {r.text[:200]}")
    return r.json()


def _athlete_id(athlete):
    for link in athlete.get("links", []) or []:
        m = _ID_RE.search(link.get("href", ""))
        if m:
            return m.group(1)
    return athlete.get("id")


def normalize(payload):
    """Return a list of flat injury item dicts."""
    out = []
    for team in payload.get("injuries", []):
        team_name = team.get("displayName")
        for it in team.get("injuries", []):
            a = it.get("athlete", {}) or {}
            details = it.get("details", {}) or {}
            out.append({
                "espn_item_id": str(it.get("id")),
                "athlete_id": _athlete_id(a),
                "athlete_name": a.get("displayName"),
                "team": (a.get("team") or {}).get("abbreviation") or team_name,
                "position": (a.get("position") or {}).get("abbreviation"),
                "status": it.get("status"),
                "injury_type": details.get("type"),
                "return_date": details.get("returnDate"),
                "item_date": it.get("date"),
                "short_comment": it.get("shortComment"),
                "long_comment": it.get("longComment"),
            })
    return out


CLEARED = "Cleared"


def diff_items(previous: dict, current: list, seen_at_iso: str):
    """previous: {espn_item_id: stored row}. Returns (events, upserts).
    An event is a new item, a status change, or an item that disappeared from ESPN's list (status -> Cleared).
    Comments changing alone do not make an event."""
    events, upserts = [], []
    seen_ids = set()
    for it in current:
        seen_ids.add(it["espn_item_id"])
        prev = previous.get(it["espn_item_id"])
        row = dict(it)
        row["last_seen_at"] = seen_at_iso
        if prev is None:
            row["first_seen_at"] = seen_at_iso
            events.append(_event(it, None, seen_at_iso))
        else:
            row["first_seen_at"] = prev.get("first_seen_at", seen_at_iso)
            if (prev.get("status") or "") != (it.get("status") or ""):
                events.append(_event(it, prev.get("status"), seen_at_iso))
        upserts.append(row)
    for item_id, prev in previous.items():
        if item_id in seen_ids or prev.get("status") == CLEARED:
            continue
        cleared = {**prev, "status": CLEARED, "last_seen_at": seen_at_iso}
        events.append(_event(cleared, prev.get("status"), seen_at_iso))
        upserts.append(cleared)
    return events, upserts


def _event(it, old_status, seen_at_iso):
    return {
        "espn_item_id": it["espn_item_id"],
        "athlete_id": it["athlete_id"],
        "athlete_name": it["athlete_name"],
        "team": it["team"],
        "position": it["position"],
        "old_status": old_status,
        "new_status": it["status"],
        "item_date": it["item_date"],
        "short_comment": it["short_comment"],
        "seen_at": seen_at_iso,
    }


def should_trigger(event, first_run=False) -> bool:
    """Decide whether an injury event justifies spending credits on an odds pull."""
    if first_run:
        return False  # the very first poll sees ~800 'new' items; never trigger on a cold start
    pos = (event.get("position") or "").upper()
    new = event.get("new_status") or ""
    old = event.get("old_status")
    if new == (old or ""):
        return False
    is_qb = pos in config.TRIGGER_ANY_CHANGE_POSITIONS
    if new == CLEARED:
        # a player dropping off the list matters when a QB who was Out/Doubtful is suddenly clear
        return is_qb and old in config.TRIGGER_STATUSES
    if old is None:
        # brand-new item: Out/Doubtful for anyone; Questionable too if it is a QB. 'Active' notes never trigger.
        return new in config.TRIGGER_STATUSES or (is_qb and new == "Questionable")
    # status change on an item we already knew
    return is_qb or new in config.TRIGGER_STATUSES
