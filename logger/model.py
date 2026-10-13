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
