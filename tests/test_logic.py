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


# ---------------------------------------------------------------- bet capture
from logger import bets as B  # noqa: E402


def test_bet_parse_ryans_message():
    p = B.parse("I took Seattle -1.5 vs Denver next week on Thurs")
    assert p["teams"] == ["Seattle Seahawks", "Denver Broncos"]
    assert p["market"] == "spreads" and p["side"] == "Seattle Seahawks" and p["line"] == -1.5
    assert "price" not in p and "stake" not in p


def test_bet_parse_order_and_aliases():
    p = B.parse("Denver +1.5 vs Seattle")                       # team order follows the text, not alias length
    assert p["side"] == "Denver Broncos" and p["line"] == 1.5
    p = B.parse("Chiefs -3.5 -110 10 bucks")
    assert p["side"] == "Kansas City Chiefs" and p["line"] == -3.5 and p["price"] == -110 and p["stake"] == 10
    p = B.parse("Over 44.5 Bears Falcons -115 $10")
    assert p["market"] == "totals" and p["side"] == "Over" and p["line"] == 44.5 and p["price"] == -115 and p["stake"] == 10
    assert p["teams"] == ["Chicago Bears", "Atlanta Falcons"]
    p = B.parse("A17 5")
    assert p["alert_id"] == 17 and p["stake"] == 5 and "line" not in p
    p = B.parse("I was on the under, no juice, Jets game")        # 'was' and 'no' are words here, not teams
    assert p["teams"] == ["New York Jets"] and p["side"] == "Under" and "line" not in p
    p = B.parse("NO -3 vs CAR")                                   # capitals are abbreviations
    assert p["teams"] == ["New Orleans Saints", "Carolina Panthers"] and p["line"] == -3
    p = B.parse("Seahawks 1.5")                                   # unsigned spread is not trusted as a line
    assert "line" not in p


def test_bet_capture_end_to_end(tmp_path, monkeypatch):
    from logger.store import LocalStore
    monkeypatch.setattr(config, "NTFY_TOPIC", "t")
    monkeypatch.setattr(config, "NTFY_FIXTURE", str(FIX / "ntfy_sample.json"))
    store = LocalStore(tmp_path)
    now = datetime(2026, 10, 11, 3, 0, tzinfo=timezone.utc)
    store.upsert("games", [
        {"id": "g_sea_den", "home_team": "Denver Broncos", "away_team": "Seattle Seahawks", "commence_time": "2026-10-16T00:15:00Z"},
        {"id": "g_sea_den_old", "home_team": "Seattle Seahawks", "away_team": "Denver Broncos", "commence_time": "2026-10-01T00:15:00Z"},
    ])
    store.insert("alerts", [{"id": 41, "event_id": "g_sea_den", "market": "spreads", "side": "Seattle Seahawks", "mb_point": -1.5,
                             "mb_price": -110, "sent_at": "2026-10-11T02:00:00Z"}])
    n = B.process(store, now, run_id=1)
    assert n == 3
    rows = {r["raw_text"]: r for r in store.tables["bets"]}
    sea = rows["I took Seattle -1.5 vs Denver next week on Thurs"]
    assert sea["event_id"] == "g_sea_den" and sea["status"] == "open" and sea["tag"] == "steam" and sea["alert_id"] == 41
    assert sea["line"] == -1.5 and sea["price"] == -110 and sea["stake"] == 5.0 and sea["received_at"] == "2026-10-11T02:41:10Z"
    tot = rows["Over 44.5 Bears Falcons -115 $10"]
    assert tot["status"] == "needs_review" and tot["event_id"] is None and tot["tag"] == "own"   # game not loaded
    assert rows["new topic works"]["status"] == "needs_review"
    assert "Line gap" not in " ".join(rows)                                                      # own alert skipped
    assert store.get_state("ntfy:since") == "1791686600"
    # second pass: nothing new, but the Bears game has appeared -> the total gets matched on retry
    store.upsert("games", [{"id": "g_chi_atl", "home_team": "Atlanta Falcons", "away_team": "Chicago Bears", "commence_time": "2026-10-18T17:00:00Z"}])
    assert B.process(store, now, run_id=2) == 1
    tot = [r for r in store.tables["bets"] if r["raw_text"].startswith("Over")][0]
    assert tot["status"] == "open" and tot["event_id"] == "g_chi_atl" and tot["line"] == 44.5 and tot["price"] == -115
    assert B.describe(sea).startswith("Seattle Seahawks -1.5 (-110) $5, Seattle Seahawks @ Denver Broncos, Thu Oct 15 6:15 PM MT")


def test_bet_capture_survives_a_bad_message(tmp_path, monkeypatch):
    """Production run 3 died with KeyError 'id' and never saved state. A failing message must not block the others,
    and messages already stored must not be re-inserted or re-confirmed on the next run."""
    from logger.store import LocalStore
    monkeypatch.setattr(config, "NTFY_TOPIC", "t")
    monkeypatch.setattr(config, "NTFY_FIXTURE", str(FIX / "ntfy_sample.json"))
    store = LocalStore(tmp_path)
    now = datetime(2026, 10, 11, 3, 0, tzinfo=timezone.utc)
    store.upsert("games", [{"id": "g_sea_den", "home_team": "Denver Broncos", "away_team": "Seattle Seahawks", "commence_time": "2026-10-16T00:15:00Z"}])
    calls = {"n": 0}
    real_insert = store.insert

    def flaky_insert(table, rows, returning=False):
        calls["n"] += 1
        if table == "bets" and rows[0]["raw_text"].startswith("Over"):
            raise RuntimeError("boom")
        return real_insert(table, rows, returning)
    monkeypatch.setattr(store, "insert", flaky_insert)
    import pytest
    with pytest.raises(RuntimeError, match="2 logged, 1 failed"):
        B.process(store, now, run_id=1)
    assert sorted(r["raw_text"][:6] for r in store.tables["bets"]) == ["I took", "new to"]
    assert store.get_state("ntfy:since") is None            # not advanced past a failure, so the message is retried
    monkeypatch.setattr(store, "insert", real_insert)
    assert B.process(store, now, run_id=2) == 1               # only the failed one is (re)logged
    assert len(store.tables["bets"]) == 3 and store.get_state("ntfy:since") == "1791686600"


def test_select_all_pages_past_the_supabase_row_cap(monkeypatch):
    """Supabase ignores limit= above its max-rows (1000). injury_items had 1650 rows, the poller loaded 1000, and the
    other 650 looked brand new on every poll (11 bogus injury triggers a poll, news pulls at the daily cap)."""
    from logger.store import SupabaseStore
    store = SupabaseStore(url="http://supabase.test", key="sb_secret_test")
    rows = [{"espn_item_id": str(i)} for i in range(1650)]
    calls = []

    class Resp:
        def __init__(self, body): self.status_code, self.text, self._b = 206, "x", body
        def json(self): return self._b

    def fake_request(method, url, json=None, headers=None, params=None, timeout=None):
        calls.append((params, headers))
        assert params["order"] == "espn_item_id"
        lo, hi = map(int, headers["Range"].split("-"))
        return Resp(rows[lo:hi + 1])
    monkeypatch.setattr(store.s, "request", fake_request)
    assert store.injury_items() == rows
    assert [h["Range"] for _, h in calls] == ["0-999", "1000-1999"]


def test_in_play_events_are_dropped():
    from datetime import timedelta
    from logger.snapshots import split_started
    now = datetime(2026, 10, 12, 1, 0, tzinfo=timezone.utc)
    evs = [{"id": "live", "commence_time": "2026-10-12T00:23:29Z"},      # Ravens @ Falcons, 37 minutes in
           {"id": "soon", "commence_time": (now + timedelta(minutes=5)).isoformat()},
           {"id": "at_kick", "commence_time": now.isoformat()},
           {"id": "no_time", "commence_time": None}]
    up, started = split_started(evs, now)
    assert [e["id"] for e in up] == ["soon", "no_time"]
    assert [e["id"] for e in started] == ["live", "at_kick"]


def test_push_carries_alert_number():
    title, msg = A.with_alert_id("Line gap: A @ B", "body\nKickoff: x", 12)
    assert title == "A12 Line gap: A @ B"
    assert msg.endswith("Log it: reply A12 (or A12 10 for $10)")
    assert B.parse("A12")["alert_id"] == 12 and B.parse("A12 10") == {**B.parse("A12 10"), "alert_id": 12, "stake": 10.0}
