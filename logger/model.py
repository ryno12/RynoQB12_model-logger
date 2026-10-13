"""The model's opinion, from the `model_lines` table the private model repo publishes (rqb/live.py).

A row gives the model's fair home margin (`fair_spread`, nflverse sign: positive = home favored) and `fair_total`,
plus `shrink_*` (how much of the model's disagreement with a line has historically been real, 0..1) and `sigma_*`
(residual spread in points). For any side at any point:

    raw   = points by which the model thinks that side is better off than the quoted number
    edge  = shrink * raw                     (the trusted part; shrink 0 means the model has earned no say)
    p     = Phi(edge / sigma)                 (probability the side covers at that number)

Labelled "unproven" everywhere it is shown until it clears the hypothesis bar in the model repo.
"""
import math


def _f(x):
    try:
        return None if x is None else float(x)
    except (TypeError, ValueError):
        return None


def phi(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def index(rows):
    return {(int(r["week"]), r["home_team"], r["away_team"]): r for r in rows if r.get("week") is not None}


def lookup(ml, game):
    if not game or game.get("week") is None:
        return None
    return ml.get((int(game["week"]), game.get("home_team"), game.get("away_team")))


def view(row, market, side, point, home_team):
    """The model's view of `side` at `point` (the points that side gets, or the total). None when it has no number."""
    if not row:
        return None
    point = _f(point)
    if market == "spreads":
        fair = _f(row.get("fair_spread"))
        if fair is None or point is None:
            return None
        margin = fair if side == home_team else -fair          # expected margin for this side
        model_point, raw = -margin, margin + point              # fair line for the side; cushion at the quoted number
        shrink, sigma = _f(row.get("shrink_spread")), _f(row.get("sigma_spread"))
    else:
        fair = _f(row.get("fair_total"))
        if fair is None or point is None:
            return None
        model_point = fair
        raw = (fair - point) if side == "Over" else (point - fair)
        shrink, sigma = _f(row.get("shrink_total")), _f(row.get("sigma_total"))
    shrink = 0.0 if shrink is None else shrink
    edge = shrink * raw
    p = phi(edge / sigma) if sigma else None
    return {"model_point": round(model_point, 1), "model_raw": round(raw, 1), "model_edge": round(edge, 2),
            "model_p": None if p is None else round(p, 3), "model_shrink": round(shrink, 2)}


def text(v, market, side):
    if not v:
        return "Model: no number for this game."
    fair = f"{side} {v['model_point']:+g}" if market == "spreads" else f"total {v['model_point']:g}"
    p = "p n/a" if v["model_p"] is None else f"p {v['model_p']:.0%}"
    return (f"Model (unproven): fair {fair}; raw lean {v['model_raw']:+g} pts for this side at MyBookie's number, "
            f"trusted edge {v['model_edge']:+.1f} (shrink {v['model_shrink']:.2f}), {p}.")


def annotate(cands, model_rows, games_by_id):
    ml = index(model_rows)
    for c in cands:
        g = games_by_id.get(c["event_id"], {})
        v = view(lookup(ml, g), c["market"], c["side"], c["mb_point"], g.get("home_team"))
        if v:
            c.update(model_point=v["model_point"], model_edge=v["model_edge"], model_p=v["model_p"])
        c["model_text"] = text(v, c["market"], c["side"])
    return cands


# ---------------------------------------------------------------------------------------------------------------------
# "Model vs MyBookie" push after each model run (Tuesday, Friday, manual): the biggest disagreements with MyBookie's
# current numbers, once per run. Not a bet signal: the trusted edge is shown so a zero shrink reads as zero.

def _nick(team):
    return (team or "?").split()[-1]


def disagreements(model_rows, games, quotes, target_book):
    """[(abs raw lean, line of text)] for every model row with a MyBookie quote, biggest first."""
    ml = index(model_rows)
    by_game = {}
    for q in quotes:
        if q.get("bookmaker") == target_book:
            by_game.setdefault(q["event_id"], {})[(q["market"], q["outcome"])] = q
    out = []
    for g in games:
        row = lookup(ml, g)
        mb = by_game.get(g["id"])
        if not row or not mb:
            continue
        home, away = g["home_team"], g["away_team"]
        q = mb.get(("spreads", home))
        if q and q.get("point") is not None:
            v = view(row, "spreads", home, q["point"], home)
            if v:
                side, lean = (home, v["model_raw"]) if v["model_raw"] >= 0 else (away, -v["model_raw"])
                edge = abs(v["model_edge"])
                out.append((lean, f"{_nick(away)} @ {_nick(home)}: fair {_nick(home)} {-_f(row['fair_spread']):+.1f} vs MB {_f(q['point']):+g} "
                                  f"-> {_nick(side)} by {lean:g} pts (trusted {edge:.1f})"))
        q = mb.get(("totals", "Over"))
        if q and q.get("point") is not None:
            v = view(row, "totals", "Over", q["point"], home)
            if v:
                side, lean = ("Over", v["model_raw"]) if v["model_raw"] >= 0 else ("Under", -v["model_raw"])
                out.append((lean, f"{_nick(away)} @ {_nick(home)}: fair total {_f(row['fair_total']):.1f} vs MB {_f(q['point']):g} "
                                  f"-> {side} by {lean:g} pts (trusted {abs(v['model_edge']):.1f})"))
    return sorted(out, key=lambda x: -x[0])


def notify_if_new(store, games, now, send, target_book, top=8):
    """Push a summary once per model run. Returns a note or None. `send(title, msg)` is ntfy.send."""
    rows = store.model_lines_open()
    if not rows:
        return None
    latest = max(str(r.get("computed_at") or "") for r in rows)
    if not latest or latest <= (store.get_state("model:notified") or ""):
        return None
    fresh = [r for r in rows if str(r.get("computed_at")) == latest]
    quotes = store.latest_quotes([g["id"] for g in games])
    lines = disagreements(fresh, games, quotes, target_book)
    if not lines:
        store.set_state("model:notified", latest)
        return f"model run {latest}: no MyBookie quotes to compare yet"
    shown = [t for _, t in lines[:top]]
    msg = "\n".join(shown) + (f"\n(+{len(lines) - top} more)" if len(lines) > top else "")
    msg += "\nUnproven: trusted = shrink x lean. The weekly report grades these."
    send(f"Model vs MyBookie: {len(fresh)} games", msg)
    store.set_state("model:notified", latest)
    return f"model run {latest}: pushed {len(shown)} of {len(lines)} disagreements"
