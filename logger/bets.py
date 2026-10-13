"""Bet capture from the ntfy topic.

Ryan posts a plain-language note to the same ntfy topic the alerts arrive on, e.g.
    "I took Seattle -1.5 vs Denver next week on Thurs"
    "A17 5"                      -> alert 17 at its line, $5
    "Chiefs -3.5 -110 10"        -> team, line, price, stake
    "Over 44.5 Bears Falcons"    -> total, team names locate the game
Every poll, the poller reads new messages, ignores its own alerts and confirmations, parses what it can,
matches the bet to an upcoming game, stores it, and pushes a one-line confirmation back. Anything it cannot
parse is stored with status needs_review so nothing is silently lost, and is re-matched on later runs
(useful when the game is not in the odds table yet).
"""
import json
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

from . import config, ntfy
from .store import iso, now_utc, parse_ts

# Odds API full names -> aliases (lower-case). City, nickname, abbreviation and common texting truncations
# ("Balt", "Cincy", "Pitt"). Ryan's "Balt -4.5" on 2026-10-12 sat in needs_review because "balt" was missing.
TEAMS = {
    "Arizona Cardinals": ["arizona", "cardinals", "ari", "ariz", "cards"],
    "Atlanta Falcons": ["atlanta", "falcons", "atl"],
    "Baltimore Ravens": ["baltimore", "ravens", "bal", "balt"],
    "Buffalo Bills": ["buffalo", "bills", "buf"],
    "Carolina Panthers": ["carolina", "panthers", "car"],
    "Chicago Bears": ["chicago", "bears", "chi"],
    "Cincinnati Bengals": ["cincinnati", "bengals", "cin", "cincy", "cinci"],
    "Cleveland Browns": ["cleveland", "browns", "cle", "clev"],
    "Dallas Cowboys": ["dallas", "cowboys", "dal"],
    "Denver Broncos": ["denver", "broncos", "den"],
    "Detroit Lions": ["detroit", "lions", "det"],
    "Green Bay Packers": ["green bay", "packers", "gb"],
    "Houston Texans": ["houston", "texans", "hou"],
    "Indianapolis Colts": ["indianapolis", "colts", "ind", "indy"],
    "Jacksonville Jaguars": ["jacksonville", "jaguars", "jax", "jac", "jags"],
    "Kansas City Chiefs": ["kansas city", "chiefs", "kc"],
    "Las Vegas Raiders": ["las vegas", "raiders", "lv"],
    "Los Angeles Chargers": ["chargers", "lac"],
    "Los Angeles Rams": ["rams", "lar"],
    "Miami Dolphins": ["miami", "dolphins", "mia", "fins"],
    "Minnesota Vikings": ["minnesota", "vikings", "min", "minn", "vikes"],
    "New England Patriots": ["new england", "patriots", "ne", "pats"],
    "New Orleans Saints": ["new orleans", "saints", "no", "nola"],
    "New York Giants": ["giants", "nyg"],
    "New York Jets": ["jets", "nyj"],
    "Philadelphia Eagles": ["philadelphia", "eagles", "phi", "philly"],
    "Pittsburgh Steelers": ["pittsburgh", "steelers", "pit", "pitt"],
    "San Francisco 49ers": ["san francisco", "49ers", "niners", "sf"],
    "Seattle Seahawks": ["seattle", "seahawks", "sea", "hawks"],
    "Tampa Bay Buccaneers": ["tampa", "tampa bay", "buccaneers", "bucs", "tb"],
    "Tennessee Titans": ["tennessee", "titans", "ten", "tenn"],
    "Washington Commanders": ["washington", "commanders", "was", "wsh", "wash"],
}
# Abbreviations that are also ordinary words: only count them when written in capitals (NO, WAS, TEN ...).
UPPER_ONLY = {"no", "was", "ten", "car", "min", "ne", "sea", "pit", "ind"}
_ALIAS = sorted(((a, t) for t, al in TEAMS.items() for a in al), key=lambda x: -len(x[0]))
_ALIAS_RE = {a: re.compile(rf"(?<![a-z0-9]){re.escape(a)}(?![a-z0-9])", re.I) for a, _ in _ALIAS}

SYSTEM_TITLES = ("Line gap", "Logged", "Could not log", "RynoQB12")
PRICE_RE = re.compile(r"(?<![\d.])([+-]1\d\d|[+-][2-9]\d\d)(?![\d.])")         # -110, +105, -120
LINE_RE = re.compile(r"(?<![\w$.])([+-]?\d{1,2}(?:\.5)?)(?![\d%.])")           # -1.5, +3, 44.5
STAKE_RE = re.compile(r"\$\s*(\d+(?:\.\d+)?)|(?<![\d.+-])(\d+(?:\.\d+)?)\s*(?:bucks|dollars|unit|units|u)\b", re.I)
ALERT_RE = re.compile(r"\bA(\d+)\b")
TOTAL_RE = re.compile(r"\b(over|under)\b\s*(\d{2}(?:\.5)?)?", re.I)
MT = ZoneInfo("America/Denver")


def fetch_messages(topic: str, since: str):
    """Poll ntfy's JSON endpoint. `since` is a unix time or a duration like '24h'."""
    r = requests.get(f"{config.NTFY_SERVER}/{topic}/json", params={"poll": "1", "since": since}, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"ntfy poll {r.status_code}: {r.text[:200]}")
    msgs = []
    for line in r.text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            m = json.loads(line)
        except ValueError:
            continue
        if m.get("event") == "message":
            msgs.append(m)
    return msgs


def is_system(m) -> bool:
    title = (m.get("title") or "")
    return any(title.startswith(t) for t in SYSTEM_TITLES)


def find_teams(text: str):
    """Teams named in the text, in the order they appear. Longest alias wins where aliases overlap."""
    hits = {}   # team -> first position
    taken = [False] * len(text)
    for alias, team in _ALIAS:
        for m in _ALIAS_RE[alias].finditer(text):
            if alias in UPPER_ONLY and m.group(0) != alias.upper():
                continue
            if any(taken[m.start():m.end()]):
                continue
            for i in range(m.start(), m.end()):
                taken[i] = True
            if team not in hits or m.start() < hits[team]:
                hits[team] = m.start()
    return [t for t, _ in sorted(hits.items(), key=lambda kv: kv[1])]


def _strip_teams(text: str) -> str:
    for alias, _ in _ALIAS:
        text = _ALIAS_RE[alias].sub(" ", text)
    return text


def parse(text: str) -> dict:
    """Best-effort parse. Returns a dict with whatever was found; a missing key means 'not stated'."""
    out = {"raw_text": text}
    m = ALERT_RE.search(text)
    if m:
        out["alert_id"] = int(m.group(1))
        text = text[:m.start()] + " " + text[m.end():]
    tot = TOTAL_RE.search(text)
    if tot:
        out["market"] = "totals"
        out["side"] = tot.group(1).capitalize()
        if tot.group(2):
            out["line"] = float(tot.group(2))
        text = text[:tot.start()] + " " + text[tot.end():]
    teams = find_teams(text)
    out["teams"] = teams
    cleaned = _strip_teams(text)
    pm = PRICE_RE.search(cleaned)
    if pm:
        out["price"] = int(pm.group(1))
        cleaned = cleaned[:pm.start()] + " " + cleaned[pm.end():]
    sm = STAKE_RE.search(cleaned)
    if sm:
        out["stake"] = float(sm.group(1) or sm.group(2))
        cleaned = cleaned[:sm.start()] + " " + cleaned[sm.end():]
    nums = LINE_RE.findall(cleaned)
    signed = [float(x) for x in nums if x[0] in "+-"]
    unsigned = [float(x) for x in nums if x[0] not in "+-"]
    if "line" not in out:
        if out.get("market") == "totals" and unsigned:
            out["line"] = unsigned[0]                 # "over 44.5" handled above; "44.5 over" lands here
        elif signed:
            out["line"] = signed[0]                   # a spread must be signed: "Seahawks -1.5"
    if "alert_id" in out and "stake" not in out and unsigned:
        out["stake"] = unsigned[0]                    # "A17 5" -> alert 17, $5
    if "market" not in out and teams:
        out["market"] = "spreads"
        out["side"] = teams[0]
    return out


def match_game(store, parsed: dict, now):
    """The next game involving the first named team (and the second, if given)."""
    teams = parsed.get("teams") or []
    if not teams:
        return None
    games = store.upcoming_games()
    cands = [g for g in games if teams[0] in (g["home_team"], g["away_team"])]
    if len(teams) > 1:   # two teams named: both must be in the game, never fall back to "team one's next game"
        cands = [g for g in cands if teams[1] in (g["home_team"], g["away_team"])]
    cands = [g for g in cands if (parse_ts(g["commence_time"]) or now) > now - timedelta(hours=4)]
    cands.sort(key=lambda g: g["commence_time"])
    return cands[0] if cands else None


def match_alert(store, parsed: dict, game, now):
    """The alert this bet followed: by number if given, else the latest alert (48h) on the same game and side."""
    if parsed.get("alert_id"):
        rows = store.select("alerts", {"select": "*", "id": f"eq.{parsed['alert_id']}"})
        return rows[0] if rows else None
    if not game or not parsed.get("side"):
        return None
    # own query: store.recent_alerts() returns a narrow column set without id / mb_point, which this needs
    rows = store.select("alerts", {"select": "id,event_id,market,side,mb_point,mb_price,sent_at",
                                   "event_id": f"eq.{game['id']}", "side": f"eq.{parsed['side']}",
                                   "sent_at": f"gte.{iso(now - timedelta(hours=48))}"})
    rows = [a for a in rows if a.get("id") is not None]
    return max(rows, key=lambda a: a["sent_at"]) if rows else None


def _row(parsed, game, alert):
    if alert:
        if parsed.get("line") is None:
            parsed["line"] = alert.get("mb_point")
            parsed.setdefault("price", alert.get("mb_price"))
        parsed.setdefault("side", alert.get("side"))
        parsed.setdefault("market", alert.get("market"))
        if not game and alert.get("event_id"):
            game = {"id": alert["event_id"], "away_team": None, "home_team": None, "commence_time": None}
    complete = bool(game and parsed.get("side") and parsed.get("line") is not None)
    return {
        "event_id": game["id"] if game else None,
        "matchup": f"{game['away_team']} @ {game['home_team']}" if game and game.get("home_team") else None,
        "kickoff": game["commence_time"] if game else None,
        "market": parsed.get("market"), "side": parsed.get("side"), "line": parsed.get("line"),
        "price": parsed.get("price", config.DEFAULT_PRICE if parsed.get("line") is not None else None),
        "stake": parsed.get("stake", config.DEFAULT_STAKE),
        "alert_id": alert["id"] if alert else None,
        "tag": "steam" if alert else "own",
        "status": "open" if complete else "needs_review",
    }


def describe(row) -> str:
    line = row["line"]
    line_txt = f"{line:+g}" if row.get("market") == "spreads" else f"{line:g}"
    when = ""
    if row.get("kickoff"):
        when = ", " + parse_ts(row["kickoff"]).astimezone(MT).strftime("%a %b %-d %-I:%M %p MT")
    return f"{row['side']} {line_txt} ({row['price']:+d}) ${row['stake']:g}, {row['matchup'] or row['event_id']}{when}. Tag: {row['tag']}."


def _confirm(row, text):
    if row["status"] == "open":
        ntfy.send("Logged", describe(row), priority="low", tags="memo")
    else:
        why = "game not in the odds table yet" if row.get("side") and row.get("line") is not None else "could not read team, number or side"
        ntfy.send("Could not log", f"\"{text[:80]}\": {why}. Saved for review; say team, number and stake, e.g. 'Seahawks -1.5 $5'.",
                  priority="low", tags="warning")


def _received_at(m) -> str:
    t = m.get("time")
    if t:
        return iso(datetime.fromtimestamp(int(t), tz=timezone.utc))
    return iso(now_utc().replace(microsecond=0))


def rematch(store, now, run_id) -> int:
    """Retry bets saved as needs_review without a game (the game may have appeared in the odds table since)."""
    fixed = 0
    for b in store.select("bets", {"select": "*", "status": "eq.needs_review", "event_id": "is.null"}):
        parsed = parse(b["raw_text"])
        game = match_game(store, parsed, now)
        if not game:
            continue
        alert = match_alert(store, parsed, game, now)
        row = _row(parsed, game, alert)
        if row["status"] != "open":
            continue
        row["run_id"] = run_id
        store.patch("bets", {"id": f"eq.{b['id']}"}, row)
        ntfy.send("Logged", describe(row) + " (matched on retry)", priority="low", tags="memo")
        fixed += 1
    return fixed


def process(store, now, run_id) -> int:
    """Read new notes from the topic, store bets, confirm. Returns the number of bets logged."""
    if not config.NTFY_TOPIC or (config.DRY_RUN and not config.NTFY_FIXTURE):
        return 0
    since = store.get_state("ntfy:since") or config.NTFY_FIRST_SINCE
    if config.NTFY_FIXTURE:
        msgs = json.load(open(config.NTFY_FIXTURE))
    else:
        msgs = fetch_messages(config.NTFY_TOPIC, since)
    seen = [s for s in (store.get_state("ntfy:seen_ids") or "").split(",") if s]
    seen_set = set(seen)
    logged = 0
    latest = None
    failed = []
    for m in msgs:
        latest = max(latest or 0, int(m.get("time") or 0))
        mid = m.get("id")
        if not mid or mid in seen_set or is_system(m):
            continue
        text = (m.get("message") or "").strip()
        if not text:
            seen.append(mid); seen_set.add(mid)
            continue
        try:
            if store.select("bets", {"select": "id", "ntfy_id": f"eq.{mid}"}):   # stored on an earlier run that died before saving state
                seen.append(mid); seen_set.add(mid)
                continue
            parsed = parse(text)
            game = match_game(store, parsed, now)
            alert = match_alert(store, parsed, game, now)
            row = _row(parsed, game, alert)
            row.update({"ntfy_id": mid, "received_at": _received_at(m), "raw_text": text, "run_id": run_id})
            store.insert("bets", [row])
            seen.append(mid); seen_set.add(mid)
            logged += 1
            _confirm(row, text)
        except Exception as ex:   # one bad message must not block the others or poison later runs
            failed.append(f"{mid}: {ex}")
    store.set_state("ntfy:seen_ids", ",".join(seen[-300:]))
    if latest and not failed:
        store.set_state("ntfy:since", str(latest))
    try:
        logged += rematch(store, now, run_id)
    except Exception as ex:  # a retry must never block new captures
        failed.append(f"rematch: {ex}")
    if failed:
        raise RuntimeError(f"{logged} logged, {len(failed)} failed: " + "; ".join(failed)[:300])
    return logged
