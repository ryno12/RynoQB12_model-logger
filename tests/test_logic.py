"""Unit checks for the pieces that must be right before the first live run. Run: pytest -q"""
import json
import os
from datetime import datetime, timezone, date
from pathlib import Path

os.environ.setdefault("DRY_RUN", "1")

from logger import alerts as A  # noqa: E402
from logger import config, espn  # noqa: E402
from logger.oddsapi import flatten  # noqa: E402
from logger.snapshots import snapshot_rows  # noqa: E402
from logger.weeks import season_week  # noqa: E402

FIX = Path(__file__).parent / "fixtures"
EVENTS = json.loads((FIX / "odds_sample.json").read_text())


def test_week_math_matches_pickem_convention():
    seasons = {2026: date(2026, 9, 8)}
    # Week 1 Thursday night 2026-09-10 8:20pm ET = 00:20Z on the 11th
    assert season_week(datetime(2026, 9, 11, 0, 20, tzinfo=timezone.utc), seasons) == (2026, 1)
    # Week 1 Monday night
    assert season_week(datetime(2026, 9, 15, 0, 15, tzinfo=timezone.utc), seasons) == (2026, 1)
    # Week 5 Sunday 2026-10-11 1pm ET
    assert season_week(datetime(2026, 10, 11, 17, 0, tzinfo=timezone.utc), seasons) == (2026, 5)
    # Week 5 Monday night ends 00:15Z Tuesday Oct 13 -> still Week 5 in New York (Mon Oct 12 evening)
    assert season_week(datetime(2026, 10, 13, 0, 15, tzinfo=timezone.utc), seasons) == (2026, 5)


def test_key_number_crossing():
    assert A._crosses_key(-2.5, -3.5)
    assert A._crosses_key(-3.0, -3.5)
    assert A._crosses_key(6.5, 7.5)
    assert not A._crosses_key(-4.0, -6.0)
    assert not A._crosses_key(-3.0, -3.0)


def test_gap_detection_on_fixture():
    quotes = list(flatten(EVENTS))
    games = {e["id"]: {"home_team": e["home_team"], "away_team": e["away_team"], "commence_time": e["commence_time"], "week": 5} for e in EVENTS}
    cands = {(c["event_id"], c["market"], c["side"]): c for c in A.find_gaps(quotes, games)}
    # 1) Chiefs home: MyBookie -3.5 vs consensus -5 -> Chiefs favorable by 1.5
    c = cands[("evt001", "spreads", "Kansas City Chiefs")]
    assert c["gap_points"] == 1.5 and not c["key_cross"]
    assert ("evt001", "spreads", "Denver Broncos") not in cands
    # 2) Eagles away: MyBookie +3.5 vs consensus +2.5 -> gap 1.0 and crosses 3
    c = cands[("evt002", "spreads", "Philadelphia Eagles")]
    assert c["gap_points"] == 1.0 and c["key_cross"]
    # 3) Over 43 vs 44.5 consensus -> Over favorable 1.5, Under not
    c = cands[("evt003", "totals", "Over")]
    assert c["gap_points"] == 1.5
    assert ("evt003", "totals", "Under") not in cands
    # 4) same point, MyBookie -100 vs -110 on Bills -> 10 cents; Dolphins -120 is worse, no alert
    c = cands[("evt004", "spreads", "Buffalo Bills")]
    assert c["gap_points"] == 0 and c["gap_cents"] == 10
    assert ("evt004", "spreads", "Miami Dolphins") not in cands
    assert ("evt004", "totals", "Over") not in cands


def test_cooldown_only_realerts_when_gap_grows():
    quotes = list(flatten(EVENTS))
    games = {e["id"]: {"home_team": e["home_team"], "away_team": e["away_team"], "commence_time": e["commence_time"]} for e in EVENTS}
    cands = A.find_gaps(quotes, games)
    now = datetime.now(timezone.utc).isoformat()
    recent = [{"event_id": "evt001", "market": "spreads", "side": "Kansas City Chiefs", "gap_points": 1.5, "gap_cents": 0, "sent_at": now},
              {"event_id": "evt003", "market": "totals", "side": "Over", "gap_points": 1.0, "gap_cents": 0, "sent_at": now}]
    kept = {(c["event_id"], c["market"], c["side"]) for c in A.filter_cooldown(cands, recent)}
    assert ("evt001", "spreads", "Kansas City Chiefs") not in kept   # same gap, suppressed
    assert ("evt003", "totals", "Over") in kept                        # gap grew 1.0 -> 1.5, re-alert
    assert ("evt002", "spreads", "Philadelphia Eagles") in kept        # never alerted


def test_snapshots_only_on_change():
    snaps, upserts = snapshot_rows(EVENTS, [], run_id=1)
    assert len(snaps) == len(upserts) == 5 * 5 * 4   # 5 events x 5 books x (2 spread + 2 total outcomes)
    assert all(s["is_first"] for s in snaps)
    # second pull, nothing changed -> no snapshots, same upserts
    snaps2, upserts2 = snapshot_rows(EVENTS, upserts, run_id=2)
    assert snaps2 == [] and len(upserts2) == 100
    # move one MyBookie line -> exactly the two outcomes of that market change
    import copy
    ev2 = copy.deepcopy(EVENTS)
    mb = next(b for b in ev2[0]["bookmakers"] if b["key"] == "mybookieag")
    for oc in mb["markets"][0]["outcomes"]:
        oc["point"] = -oc["point"] if False else (oc["point"] - 1 if oc["point"] < 0 else oc["point"] + 1)
    snaps3, _ = snapshot_rows(ev2, upserts, run_id=3)
    assert len(snaps3) == 2 and not any(s["is_first"] for s in snaps3)


def test_injury_diff_and_triggers():
    payload = json.loads((FIX / "espn_sample.json").read_text())
    items = espn.normalize(payload)
    assert items and all(i["espn_item_id"] for i in items)
    events, upserts = espn.diff_items({}, items, "2026-10-10T00:00:00Z")
    assert len(events) == len(items) and all(e["old_status"] is None for e in events)
    # cold start never triggers
    assert not any(espn.should_trigger(e, first_run=True) for e in events)
    # a known item flipping Questionable -> Out triggers; -> Active does not (non-QB)
    prev = {u["espn_item_id"]: u for u in upserts}
    changed = [dict(i) for i in items]
    changed[0]["status"] = "Out" if changed[0]["status"] != "Out" else "Doubtful"
    ev2, _ = espn.diff_items(prev, changed, "2026-10-10T01:00:00Z")
    assert len(ev2) == 1 and espn.should_trigger(ev2[0])
    # an item that disappears becomes Cleared; non-QB Cleared does not trigger
    ev3, up3 = espn.diff_items(prev, items[1:], "2026-10-10T02:00:00Z")
    assert len(ev3) == 1 and ev3[0]["new_status"] == espn.CLEARED
    assert not espn.should_trigger(ev3[0]) or (items[0]["position"] == "QB" and items[0]["status"] in config.TRIGGER_STATUSES)
    # QB rules
    qb_new_q = {"position": "QB", "old_status": None, "new_status": "Questionable"}
    qb_new_active = {"position": "QB", "old_status": None, "new_status": "Active"}
    qb_change = {"position": "QB", "old_status": "Questionable", "new_status": "Active"}
    qb_cleared = {"position": "QB", "old_status": "Out", "new_status": espn.CLEARED}
    wr_new_q = {"position": "WR", "old_status": None, "new_status": "Questionable"}
    assert espn.should_trigger(qb_new_q) and not espn.should_trigger(qb_new_active)
    assert espn.should_trigger(qb_change) and espn.should_trigger(qb_cleared)
    assert not espn.should_trigger(wr_new_q)


def test_message_format():
    quotes = list(flatten(EVENTS))
    games = {e["id"]: {"home_team": e["home_team"], "away_team": e["away_team"], "commence_time": e["commence_time"]} for e in EVENTS}
    c = next(c for c in A.find_gaps(quotes, games) if c["event_id"] == "evt002")
    title, msg = A.build_message(c, "scheduled:monday_open")
    assert "Philadelphia Eagles +3.5" in msg and "consensus +2.5" in msg and "crosses key number" in msg
    assert title.startswith("Line gap:")


def test_pre_and_close_windows():
    from datetime import timedelta
    from logger.run_poll import _close_due, _pre_due
    now = datetime.now(timezone.utc)
    def g(mins, pre=None, close=None):
        return {"id": f"g{mins}", "commence_time": (now + timedelta(minutes=mins)).isoformat(), "pre_pull_at": pre, "close_pull_at": close,
                "home_team": "H", "away_team": "A"}
    games = [g(30), g(110), g(200), g(110, pre="done"), g(30, close="done")]
    assert [x["id"] for x in _close_due(games, now)] == ["g30"]
    assert [x["id"] for x in _pre_due(games, now)] == ["g110"]
