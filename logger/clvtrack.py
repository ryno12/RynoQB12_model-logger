"""Does MyBookie's line move toward the model? Closing-line value of the model's leans (2026-11-10).

Win-loss needs ~1,000 picks to separate a 55% bettor from a lucky 52.4% one; line movement needs ~80 games to show a
+0.3-point drift, so this is how the model earns trust. Per game: the EARLIEST pre-kickoff v2 model version (it has the
most time to be proven right) at which MyBookie already had a number, that number (its latest snapshot at or before computed_at) and
MyBookie's close (last snapshot before kickoff).

    spreads  gap = fair home margin + MB home point;  clv = sign(gap) * (MB home point then - MB home point at close)
    totals   gap = fair total - MB total;             clv = sign(gap) * (MB total at close - MB total then)

clv > 0: MyBookie moved toward the model. A game counts at a threshold when |gap| >= it.

L01, pre-registered 2026-11-10 (model repo CLAUDE.md, "Live hypotheses"): totals with |gap| >= 1, games from 2026
Week 10 on (Weeks 7-9 suggested the rule, so they do not count). Checkpoint at 80 games: adopt if mean CLV >= +0.30
and mean / standard error >= 1.645; drop if mean <= 0; otherwise keep watching to 160 games or the season's end.
"""
import math
from statistics import mean, stdev

from .store import parse_ts

L01 = dict(market="totals", gap=1.0, season=2026, from_week=10, n=80, mean=0.30, z=1.645)


def per_game(history, games, mb_snaps):
    """[{season, week, game, market, gap, clv}] one row per game and market."""
    gk = {(g.get("season"), g.get("week"), g["home_team"], g["away_team"]): g for g in games}
    snaps = {}
    for s in mb_snaps:
        if s.get("point") is None:
            continue
        snaps.setdefault((s["event_id"], s["market"], s["outcome"]), []).append((parse_ts(s["seen_at"]), float(s["point"])))
    for v in snaps.values():
        v.sort(key=lambda x: x[0])
    versions = {}
    for h in history:
        if str(h.get("model_version") or "").startswith("v1"):
            continue
        g = gk.get((h.get("season"), h.get("week"), h["home_team"], h["away_team"]))
        if not g or g.get("home_score") is None:   # finished games only: an unplayed game has no close yet
            continue
        t, ko = parse_ts(h["computed_at"]), parse_ts(g["commence_time"])
        if not t or not ko or t >= ko:
            continue
        versions.setdefault(g["id"], []).append((t, h, g))
    out = []
    for vs in versions.values():
        vs.sort(key=lambda x: x[0])
        g = vs[0][2]
        ko = parse_ts(g["commence_time"])
        for market, outcome, key in (("spreads", g["home_team"], "fair_spread"), ("totals", "Over", "fair_total")):
            xs = snaps.get((g["id"], market, outcome), [])
            close = [p for ts, p in xs if ts < ko]
            # the earliest model version at which MyBookie already had a number (two-weeks-out versions often predate it)
            pick = next(((t, h, [p for ts, p in xs if ts <= t]) for t, h, _ in vs
                         if h.get(key) is not None and any(ts <= t for ts, _p in xs)), None)
            if not pick or not close:
                continue
            t, h, then = pick
            fair = h[key]
            p0, p1 = then[-1], close[-1]
            gap = float(fair) + p0 if market == "spreads" else float(fair) - p0
            move = (p0 - p1) if market == "spreads" else (p1 - p0)
            inp = _inputs(h)
            out.append({"season": g.get("season"), "week": g.get("week"), "game": f"{g['away_team']} @ {g['home_team']}",
                        "market": market, "gap": gap, "clv": math.copysign(1, gap) * move if gap else 0.0,
                        "dome": inp.get("dome"), "wind": inp.get("wind_out")})
    return out


def _inputs(h):
    inp = h.get("inputs") or {}
    if isinstance(inp, str):
        import json
        inp = json.loads(inp)
    return inp


def windy(r):
    """Outdoor game whose model forecast (at the version measured) was 10+ mph. A report split only, not part of L01."""
    return r.get("dome") == 0 and (r.get("wind") or 0) >= 10


# W01, pre-registered 2026-11-10 (model repo CLAUDE.md, "Live hypotheses"): outdoor games whose late forecast is
# 10-14.9 mph go under MyBookie's closing total. Forecast = wind_out of the last pre-kickoff model version computed 12h
# to 4 days before kickoff (the Friday run for Sunday games, the Tuesday run for Thursday games).
W01 = dict(season=2026, from_week=10, lo=10.0, hi=15.0, min_h=12, max_h=96)


def w01_rows(history, games, mb_snaps):
    from datetime import timedelta
    gk = {(g.get("season"), g.get("week"), g["home_team"], g["away_team"]): g for g in games}
    close = {}
    for s in mb_snaps:
        if s.get("market") == "totals" and s.get("outcome") == "Over" and s.get("point") is not None:
            close.setdefault(s["event_id"], []).append((parse_ts(s["seen_at"]), float(s["point"])))
    best = {}
    for h in history:
        if str(h.get("model_version") or "").startswith("v1"):
            continue
        g = gk.get((h.get("season"), h.get("week"), h["home_team"], h["away_team"]))
        if not g or g.get("season") != W01["season"] or (g.get("week") or 0) < W01["from_week"] or g.get("home_score") is None:
            continue
        t, ko = parse_ts(h["computed_at"]), parse_ts(g["commence_time"])
        if not t or not ko or not (ko - timedelta(hours=W01["max_h"]) <= t <= ko - timedelta(hours=W01["min_h"])):
            continue
        if g["id"] not in best or t > best[g["id"]][0]:
            best[g["id"]] = (t, h, g)
    out = []
    for t, h, g in best.values():
        inp, ko = _inputs(h), parse_ts(g["commence_time"])
        w = inp.get("wind_out")
        cl = [p for ts, p in sorted(close.get(g["id"], [])) if ts < ko]
        if inp.get("dome") != 0 or w is None or not (W01["lo"] <= w < W01["hi"]) or not cl:
            continue
        total = float(g["home_score"]) + float(g["away_score"])
        out.append({"game": f"{g['away_team']} @ {g['home_team']}", "week": g.get("week"), "wind": w, "close": cl[-1],
                    "total": total, "result": "won" if total < cl[-1] else "lost" if total > cl[-1] else "push"})
    return out


def summary(rows):
    n = len(rows)
    if not n:
        return {"n": 0, "mean": None, "se": None, "z": None, "toward": None, "away": None}
    m = mean(r["clv"] for r in rows)
    se = stdev(r["clv"] for r in rows) / math.sqrt(n) if n > 1 else None
    return {"n": n, "mean": m, "se": se, "z": (m / se) if se else None,
            "toward": sum(r["clv"] > 0 for r in rows) / n, "away": sum(r["clv"] < 0 for r in rows) / n}


def l01_status(rows):
    s = summary([r for r in rows if r["market"] == L01["market"] and abs(r["gap"]) >= L01["gap"]
                 and r["season"] == L01["season"] and (r["week"] or 0) >= L01["from_week"]])
    if s["n"] < L01["n"]:
        verdict = f"watching: {s['n']} of {L01['n']} games"
    elif s["mean"] >= L01["mean"] and (s["z"] or 0) >= L01["z"]:
        verdict = "ADOPT: raise the model's totals trust (Model edge push fires on totals 1+ pts off MyBookie)"
    elif s["mean"] <= 0:
        verdict = "DROP: MyBookie does not move toward the model's totals"
    else:
        verdict = "not settled: keep watching to 160 games or the season's end"
    return s, verdict
