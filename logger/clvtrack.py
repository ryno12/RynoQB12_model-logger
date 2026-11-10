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
            out.append({"season": g.get("season"), "week": g.get("week"), "game": f"{g['away_team']} @ {g['home_team']}",
                        "market": market, "gap": gap, "clv": math.copysign(1, gap) * move if gap else 0.0})
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
