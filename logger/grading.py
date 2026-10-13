"""Grade bets and alerts once a game is final, and record closing-line value.

Definitions (Ryan can override; these are the ones the weekly report will use):
  result      spreads: side margin + line  > 0 won, < 0 lost, = 0 push.  totals: Over wins if total > line, Under if <.
  profit      dollars. won: stake * (price/100 if price > 0 else 100/|price|); lost: -stake; push/void: 0.
  units       profit / UNIT_DOLLARS (the $5 flat stake), stored in bets.result_units.
  close       the last odds_snapshots row per (book, market, outcome) with seen_at before kickoff. close_seen_at says how
              near kickoff that was (a throttled run may have left it hours out).
  clv_points  positive = the bet got a better line than the close. spreads: line - close_point (took -1.5, closed -2.5
              -> +1). totals: Over: close - line; Under: line - close.
  clv_cents   price - close_price, only when the points are equal (took -110, closed -120 -> +10); else null.
  alerts      graded as a $5 flat bet at the alerted MyBookie line, plus whether MyBookie's gap to consensus shrank by
              close (moved_toward_consensus), which is the "was this steam real" question.
"""
from datetime import timedelta
from statistics import median

from . import config, scores
from .store import iso, parse_ts


# ---------------------------------------------------------------- pure math
def grade_side(market, side, line, home_team, away_team, home_score, away_score):
    if home_score is None or away_score is None or line is None:
        return None
    line = float(line)
    if market == "spreads":
        if side == home_team:
            margin = home_score - away_score
        elif side == away_team:
            margin = away_score - home_score
        else:
            return None
        adj = margin + line
    elif market == "totals":
        total = home_score + away_score
        if side == "Over":
            adj = total - line
        elif side == "Under":
            adj = line - total
        else:
            return None
    else:
        return None
    return "won" if adj > 0 else "lost" if adj < 0 else "push"


def profit(result, stake, price):
    if result == "won":
        stake, price = float(stake), int(price if price is not None else config.DEFAULT_PRICE)
        return round(stake * (price / 100 if price > 0 else 100 / -price), 2)
    if result == "lost":
        return -float(stake)
    return 0.0


def closing_quotes(snapshots, kickoff):
    """{(bookmaker, market, outcome): snapshot} using the last row seen before kickoff. In-play rows are ignored."""
    kt = parse_ts(kickoff)
    best = {}
    for s in snapshots:
        st = parse_ts(s.get("seen_at"))
        if not st or (kt and st >= kt):
            continue
        k = (s["bookmaker"], s["market"], s["outcome"])
        if k not in best or st > parse_ts(best[k]["seen_at"]):
            best[k] = s
    return best


def consensus_close(close, market, outcome):
    rows = [close[(b, market, outcome)] for b in config.CONSENSUS_BOOKS if (b, market, outcome) in close
            and close[(b, market, outcome)].get("point") is not None]
    if len(rows) < config.MIN_CONSENSUS_BOOKS:
        return None, None, None
    pts = [float(r["point"]) for r in rows]
    prices = [int(r["price"]) for r in rows if r.get("price") is not None]
    return median(pts), (int(median(prices)) if prices else None), max(parse_ts(r["seen_at"]) for r in rows)


def clv(market, side, line, price, close_point, close_price):
    """(points, cents) in the bettor's favor; cents only when points match."""
    if line is None or close_point is None:
        return None, None
    line, close_point = float(line), float(close_point)
    if market == "spreads" or side == "Under":
        pts = line - close_point
    else:  # totals Over
        pts = close_point - line
    cents = None
    if abs(pts) < 1e-9 and price is not None and close_price is not None:
        cents = int(price) - int(close_price)
    return round(pts, 2), cents


def gap_at(market, side, mb_point, cons_point):
    """The alert gap convention from alerts.find_gaps: positive = MyBookie favors this side."""
    if mb_point is None or cons_point is None:
        return None
    mb_point, cons_point = float(mb_point), float(cons_point)
    if market == "spreads" or side == "Under":
        return round(mb_point - cons_point, 2)
    return round(cons_point - mb_point, 2)


# ---------------------------------------------------------------- orchestration
def update_scores(store, now):
    """Fetch finals for games that kicked off long enough ago and have no score yet. Returns games finalized now."""
    pending = store.games_awaiting_score(now - timedelta(hours=config.GRADE_AFTER_HOURS))
    if not pending:
        return [], 0
    boards = {}
    finalized, unresolved = [], 0
    for g in pending:
        ct = parse_ts(g["commence_time"])
        if ct < now - timedelta(days=config.GRADE_GIVEUP_DAYS):
            store.patch("games", {"id": f"eq.{g['id']}"}, {"espn_status": "unresolved", "final_at": iso(now)})
            unresolved += 1
            continue
        d = scores.et_date(g["commence_time"])
        if d not in boards:
            boards[d] = scores.normalize(scores.fetch_scoreboard(d))
        row = scores.match_game(g, boards[d])
        if not row:
            continue
        if row["status"] in scores.DEAD:
            store.patch("games", {"id": f"eq.{g['id']}"}, {"espn_status": row["status"], "final_at": iso(now)})
            g = {**g, "espn_status": row["status"]}
            finalized.append(g)
        elif row["status"] == scores.FINAL and row["completed"]:
            upd = {"home_score": row["home_score"], "away_score": row["away_score"], "espn_status": scores.FINAL, "final_at": iso(now)}
            store.patch("games", {"id": f"eq.{g['id']}"}, upd)
            finalized.append({**g, **upd})
    return finalized, unresolved


def _close_for(store, game, market, outcome):
    """(close dict, mb quote, cons point, cons price, seen_at) for one market/outcome of a game."""
    close = closing_quotes(store.snapshots_for(game["id"]), game["commence_time"])
    mb = close.get((config.TARGET_BOOK, market, outcome))
    cons_pt, cons_price, cons_seen = consensus_close(close, market, outcome)
    seen = max([t for t in (parse_ts(mb["seen_at"]) if mb else None, cons_seen) if t], default=None)
    return mb, cons_pt, cons_price, seen


def grade_bets(store, games, now):
    """Grade open bets on the given finalized games. Returns the number graded."""
    by_id = {g["id"]: g for g in games}
    n = 0
    for b in store.open_bets():
        g = by_id.get(b.get("event_id"))
        if not g:
            continue
        if g.get("espn_status") in scores.DEAD:
            store.patch("bets", {"id": f"eq.{b['id']}"}, {"status": "void", "profit": 0, "result_units": 0, "settled_at": iso(now), "graded_at": iso(now)})
            n += 1
            continue
        result = grade_side(b["market"], b["side"], b["line"], g["home_team"], g["away_team"], g["home_score"], g["away_score"])
        if result is None:
            store.patch("bets", {"id": f"eq.{b['id']}"}, {"status": "needs_review", "note": "could not grade: side/market/line did not fit the game"})
            continue
        p = profit(result, b.get("stake") or config.DEFAULT_STAKE, b.get("price"))
        upd = {"status": result, "profit": p, "result_units": round(p / config.UNIT_DOLLARS, 3), "settled_at": iso(now), "graded_at": iso(now)}
        mb, cons_pt, cons_price, seen = _close_for(store, g, b["market"], b["side"])
        if seen:
            upd["close_seen_at"] = iso(seen)
        if mb:
            upd.update(close_mb_point=mb.get("point"), close_mb_price=mb.get("price"))
            upd["clv_mb_points"], upd["clv_mb_cents"] = clv(b["market"], b["side"], b["line"], b.get("price"), mb.get("point"), mb.get("price"))
        if cons_pt is not None:
            upd.update(close_cons_point=cons_pt, close_cons_price=cons_price)
            upd["clv_points"], upd["clv_cents"] = clv(b["market"], b["side"], b["line"], b.get("price"), cons_pt, cons_price)
        store.patch("bets", {"id": f"eq.{b['id']}"}, upd)
        n += 1
    return n


def grade_alerts(store, games, now):
    """Score every ungraded alert on the finalized games as a $5 flat bet, and record whether its gap closed."""
    n = 0
    for g in games:
        for a in store.ungraded_alerts(g["id"]):
            upd = {"graded_at": iso(now)}
            if g.get("espn_status") in scores.DEAD:
                upd.update(result="void", profit=0)
            else:
                result = grade_side(a["market"], a["side"], a["mb_point"], g["home_team"], g["away_team"], g["home_score"], g["away_score"])
                upd["result"] = result
                upd["profit"] = profit(result, config.UNIT_DOLLARS, a.get("mb_price")) if result else None
            mb, cons_pt, cons_price, seen = _close_for(store, g, a["market"], a["side"])
            if seen:
                upd["close_seen_at"] = iso(seen)
            if mb:
                upd.update(close_mb_point=mb.get("point"), close_mb_price=mb.get("price"))
            if cons_pt is not None:
                upd.update(close_cons_point=cons_pt, close_cons_price=cons_price)
            if mb and cons_pt is not None:
                cg = gap_at(a["market"], a["side"], mb.get("point"), cons_pt)
                upd["close_gap_points"] = cg
                if cg is not None and a.get("gap_points") is not None:
                    upd["moved_toward_consensus"] = cg < float(a["gap_points"])
            store.patch("alerts", {"id": f"eq.{a['id']}"}, upd)
            n += 1
    return n


def run(store, now):
    """One grading pass. Returns a short note or None when nothing happened."""
    games, unresolved = update_scores(store, now)
    if not games and not unresolved:
        return None
    nb = grade_bets(store, games, now)
    na = grade_alerts(store, games, now)
    finals = "; ".join(f"{g['away_team']} {g.get('away_score')} @ {g['home_team']} {g.get('home_score')}" if g.get("espn_status") == scores.FINAL
                       else f"{g['away_team']} @ {g['home_team']} {g.get('espn_status')}" for g in games)
    note = f"graded: {len(games)} finals ({finals}), {nb} bets, {na} alerts"
    if unresolved:
        note += f", {unresolved} games unresolved after {config.GRADE_GIVEUP_DAYS} days"
    return note
