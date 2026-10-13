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


# ---------------------------------------------------------------- grading + CLV
from logger import grading as G, scores as S  # noqa: E402

RAV = dict(home_team="Atlanta Falcons", away_team="Baltimore Ravens", home_score=23, away_score=30)


def test_grade_side_spreads_and_totals():
    gs = lambda m, side, line: G.grade_side(m, side, line, **RAV)  # noqa: E731
    assert gs("spreads", "Baltimore Ravens", -4.5) == "won"
    assert gs("spreads", "Baltimore Ravens", -7) == "push"
    assert gs("spreads", "Baltimore Ravens", -7.5) == "lost"
    assert gs("spreads", "Atlanta Falcons", 6.5) == "lost"
    assert gs("spreads", "Atlanta Falcons", 7.5) == "won"
    assert gs("totals", "Over", 52.5) == "won" and gs("totals", "Under", 52.5) == "lost" and gs("totals", "Over", 53) == "push"
    assert gs("spreads", "Denver Broncos", -3) is None
    assert G.grade_side("spreads", "Atlanta Falcons", -3, "Atlanta Falcons", "Baltimore Ravens", None, None) is None


def test_profit_and_units():
    assert G.profit("won", 5, -110) == 4.55 and G.profit("won", 5, -115) == 4.35 and G.profit("won", 5, 150) == 7.5
    assert G.profit("lost", 5, -110) == -5.0 and G.profit("push", 5, -110) == 0.0 and G.profit("void", 5, None) == 0.0


def test_closing_quotes_take_last_pre_kickoff_row():
    kick = "2026-10-12T00:23:29Z"
    snaps = [
        {"bookmaker": "mybookieag", "market": "spreads", "outcome": "Baltimore Ravens", "point": -3.0, "price": -110, "seen_at": "2026-10-11T13:56:00Z"},
        {"bookmaker": "mybookieag", "market": "spreads", "outcome": "Baltimore Ravens", "point": -3.5, "price": -110, "seen_at": "2026-10-11T22:24:00Z"},
        {"bookmaker": "mybookieag", "market": "spreads", "outcome": "Baltimore Ravens", "point": -6.5, "price": -110, "seen_at": "2026-10-12T01:27:00Z"},  # in-play
        {"bookmaker": "draftkings", "market": "spreads", "outcome": "Baltimore Ravens", "point": -4.5, "price": -108, "seen_at": "2026-10-11T22:24:00Z"},
        {"bookmaker": "fanduel", "market": "spreads", "outcome": "Baltimore Ravens", "point": -5.0, "price": -110, "seen_at": "2026-10-11T22:24:00Z"},
    ]
    close = G.closing_quotes(snaps, kick)
    assert close[("mybookieag", "spreads", "Baltimore Ravens")]["point"] == -3.5
    pt, price, seen = G.consensus_close(close, "spreads", "Baltimore Ravens")
    assert (pt, price) == (-4.75, -109) and seen.isoformat().startswith("2026-10-11T22:24")
    assert G.consensus_close(close, "totals", "Over") == (None, None, None)


def test_clv_sign_conventions():
    assert G.clv("spreads", "Seattle Seahawks", -1.5, -110, -2.5, -110) == (1.0, None)     # took -1.5, closed -2.5: +1
    assert G.clv("spreads", "Seattle Seahawks", -1.5, -110, -1.5, -120) == (0.0, 10)      # same point, 10 cents better
    assert G.clv("spreads", "Seattle Seahawks", -3.5, -110, -2.5, -110) == (-1.0, None)
    assert G.clv("totals", "Over", 44.5, -110, 46.0, -110) == (1.5, None)                # Over 44.5 closed 46
    assert G.clv("totals", "Under", 44.5, -110, 46.0, -110) == (-1.5, None)
    assert G.clv("totals", "Over", 44.5, -110, None, None) == (None, None)
    assert G.gap_at("spreads", "Baltimore Ravens", -3.5, -4.5) == 1.0 and G.gap_at("totals", "Over", 41.5, 43.5) == 2.0


def test_scoreboard_fixture_matches_odds_api_games(monkeypatch):
    monkeypatch.setattr(config, "SCORES_FIXTURE", str(FIX / "espn_scoreboard_sample.json"))
    assert S.et_date("2026-10-12T00:23:29Z") == "20261011"       # Sunday night game belongs to Sunday's slate
    rows = S.normalize(S.fetch_scoreboard("20261011"))
    assert len(rows) == 14 and all(r["status"] == "STATUS_FINAL" for r in rows)
    g = {"home_team": "Atlanta Falcons", "away_team": "Baltimore Ravens", "commence_time": "2026-10-12T00:23:29Z"}
    r = S.match_game(g, rows)
    assert (r["home_score"], r["away_score"]) == (23, 30)
    assert S.match_game({**g, "commence_time": "2026-10-19T00:23:29Z"}, rows) is None   # wrong week


def test_grading_end_to_end(tmp_path, monkeypatch):
    from logger.store import LocalStore
    monkeypatch.setattr(config, "SCORES_FIXTURE", str(FIX / "espn_scoreboard_sample.json"))
    store = LocalStore(tmp_path)
    now = datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc)
    store.upsert("games", [
        {"id": "g_bal", "home_team": "Atlanta Falcons", "away_team": "Baltimore Ravens", "commence_time": "2026-10-12T00:23:29Z"},
        {"id": "g_buf", "home_team": "Los Angeles Rams", "away_team": "Buffalo Bills", "commence_time": "2026-10-13T00:15:00Z"},
        {"id": "g_sea", "home_team": "Denver Broncos", "away_team": "Seattle Seahawks", "commence_time": "2026-10-16T00:15:00Z"},   # future
        {"id": "g_old", "home_team": "Nowhere", "away_team": "Nobody", "commence_time": "2026-10-01T00:00:00Z"},                 # never resolved
    ])
    def q(ev, bk, mk, oc, pt, pr, seen):
        return {"event_id": ev, "bookmaker": bk, "market": mk, "outcome": oc, "point": pt, "price": pr, "seen_at": seen}
    store.insert("odds_snapshots", [
        q("g_bal", "mybookieag", "spreads", "Baltimore Ravens", -3.5, -110, "2026-10-11T22:24:00Z"),
        q("g_bal", "draftkings", "spreads", "Baltimore Ravens", -4.5, -108, "2026-10-11T22:24:00Z"),
        q("g_bal", "fanduel", "spreads", "Baltimore Ravens", -5.0, -110, "2026-10-11T22:24:00Z"),
        q("g_bal", "mybookieag", "spreads", "Baltimore Ravens", -6.5, -110, "2026-10-12T01:27:00Z"),   # in-play, ignored
        q("g_buf", "mybookieag", "spreads", "Buffalo Bills", 1.5, -125, "2026-10-13T00:00:00Z"),
        q("g_buf", "draftkings", "spreads", "Buffalo Bills", 2.5, -110, "2026-10-13T00:00:00Z"),
        q("g_buf", "fanduel", "spreads", "Buffalo Bills", 2.5, -112, "2026-10-13T00:00:00Z"),
    ])
    store.insert("bets", [
        {"id": 4, "event_id": "g_bal", "market": "spreads", "side": "Baltimore Ravens", "line": -4.5, "price": -110, "stake": 5, "status": "open", "raw_text": "Balt -4.5 (-110)"},
        {"id": 5, "event_id": "g_buf", "market": "spreads", "side": "Buffalo Bills", "line": 3.5, "price": -115, "stake": 5, "status": "open", "raw_text": "Buf +3.5 (-115)"},
        {"id": 7, "event_id": "g_sea", "market": "spreads", "side": "Seattle Seahawks", "line": -1.5, "price": -110, "stake": 5, "status": "open", "raw_text": "Seahawks -1.5"},
        {"id": 8, "event_id": None, "market": None, "side": None, "line": None, "price": None, "stake": 5, "status": "needs_review", "raw_text": "triggered"},
    ])
    store.insert("alerts", [{"id": 43, "event_id": "g_bal", "market": "spreads", "side": "Baltimore Ravens", "mb_point": -3.5, "mb_price": -110,
                             "cons_point": -4.5, "gap_points": 1.0, "sent_at": "2026-10-11T22:30:00Z"},
                            {"id": 44, "event_id": "g_bal", "market": "spreads", "side": "Baltimore Ravens", "mb_point": -21.5, "mb_price": -110,
                             "cons_point": -23.5, "gap_points": 2.0, "sent_at": "2026-10-12T02:30:00Z"}])   # sent in-play
    note = G.run(store, now)
    assert note.startswith("graded: 2 finals (Baltimore Ravens 30 @ Atlanta Falcons 23; Buffalo Bills 37 @ Los Angeles Rams 34), 2 bets, 2 alerts")
    assert "1 games unresolved" in note
    bets = {b["id"]: b for b in store.tables["bets"]}
    bal, buf = bets[4], bets[5]
    assert bal["status"] == "won" and bal["profit"] == 4.55 and bal["result_units"] == 0.91
    assert bal["close_mb_point"] == -3.5 and bal["close_cons_point"] == -4.75 and bal["clv_points"] == 0.25 and bal["clv_mb_points"] == -1.0
    assert bal["close_seen_at"].startswith("2026-10-11T22:24")
    assert buf["status"] == "won" and buf["profit"] == 4.35 and buf["clv_points"] == 1.0 and buf["clv_mb_points"] == 2.0
    assert bets[7]["status"] == "open" and bets[8]["status"] == "needs_review"
    games = {g["id"]: g for g in store.tables["games"]}
    assert games["g_bal"]["home_score"] == 23 and games["g_bal"]["espn_status"] == "STATUS_FINAL"
    assert games["g_sea"].get("final_at") is None and games["g_old"]["espn_status"] == "unresolved"
    a, inplay = store.tables["alerts"]
    assert a["result"] == "won" and a["profit"] == 4.55 and a["close_gap_points"] == 1.25 and a["moved_toward_consensus"] is False
    assert inplay["result"] == "void" and inplay["profit"] == 0 and inplay["graded_at"]   # in-play line, not a pre-game suggestion
    assert G.run(store, now) is None   # nothing left to grade; no second pass over the same games


def test_truncated_city_names_rematch_on_retry(tmp_path):
    """"Balt -4.5 (-110)" (2026-10-12) must parse, and a needs_review row with that text must be fixed by the retry pass
    once the next Ravens game is in the games table."""
    from logger.store import LocalStore
    p = B.parse("Balt -4.5 (-110)")
    assert p["teams"] == ["Baltimore Ravens"] and p["line"] == -4.5 and p["price"] == -110
    assert B.parse("Cincy over 47")["teams"] == ["Cincinnati Bengals"] and B.parse("Pitt +3")["teams"] == ["Pittsburgh Steelers"]
    assert B.parse("a wash of a game")["teams"] == ["Washington Commanders"]   # lower-case truncation is accepted; only the
    store = LocalStore(tmp_path)                                               # 3-letter codes in UPPER_ONLY need capitals
    now = datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc)
    store.upsert("games", [{"id": "g_bal_cle", "home_team": "Cleveland Browns", "away_team": "Baltimore Ravens", "commence_time": "2026-10-18T17:00:00Z"}])
    store.insert("bets", [{"id": 4, "ntfy_id": "x", "raw_text": "Balt -4.5 (-110)", "event_id": None, "status": "needs_review", "stake": 5.0}])
    assert B.rematch(store, now, run_id=9) == 1
    b = store.tables["bets"][0]
    assert b["status"] == "open" and b["event_id"] == "g_bal_cle" and b["side"] == "Baltimore Ravens" and b["line"] == -4.5 and b["tag"] == "own"
