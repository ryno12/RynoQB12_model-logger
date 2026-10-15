"""Compare MyBookie's current quotes with the comparison-book median and decide what to alert on.

Favorable side logic:
  spreads: the side whose MyBookie point is HIGHER than the consensus point for that side gets more cushion.
           e.g. consensus home -5, MyBookie home -3.5  -> home at MyBookie is favorable (gap +1.5).
  totals:  MyBookie total below consensus -> Over is favorable; above -> Under is favorable.
Price-only gaps (same point, MyBookie price at least ALERT_GAP_CENTS better) also alert.
Key-number crossing: the two points straddle 3 or 7 (in absolute value) on spreads.
"""
from datetime import timedelta
from statistics import median
from zoneinfo import ZoneInfo

from . import config
from .store import now_utc, parse_ts

DENVER = ZoneInfo("America/Denver")


def _group(quotes):
    g = {}
    for q in quotes:
        g.setdefault((q["event_id"], q["market"], q["outcome"]), {})[q["bookmaker"]] = q
    return g


def _crosses_key(a, b):
    if a is None or b is None:
        return False
    lo, hi = sorted((abs(a), abs(b)))
    if lo == hi:
        return False
    # -2.5 vs -3.5 straddles 3; -3 vs -3.5 moves on/off the key number; -4 vs -6 crosses nothing.
    return any(lo <= k <= hi for k in config.KEY_NUMBERS)


def _consensus(books: dict):
    rows = [v for k, v in books.items() if k in config.CONSENSUS_BOOKS and v.get("point") is not None]
    if len(rows) < config.MIN_CONSENSUS_BOOKS:
        return None, None, 0
    pts = [float(r["point"]) for r in rows]
    prices = [int(r["price"]) for r in rows if r.get("price") is not None]
    return median(pts), (int(median(prices)) if prices else None), len(rows)


def find_gaps(quotes, games_by_id):
    """Return candidate alerts (unfiltered by cooldown)."""
    out = []
    grouped = _group(quotes)
    for (event_id, market, outcome), books in grouped.items():
        mb = books.get(config.TARGET_BOOK)
        if not mb or mb.get("point") is None:
            continue
        cons_pt, cons_price, n = _consensus(books)
        if cons_pt is None:
            continue
        mb_pt = float(mb["point"])
        mb_price = int(mb["price"]) if mb.get("price") is not None else None
        game = games_by_id.get(event_id, {})

        if market == "spreads":
            gap = mb_pt - cons_pt                     # positive = this side gets more points at MyBookie
            side = outcome
            key_cross = gap > 0 and _crosses_key(mb_pt, cons_pt)
        else:  # totals
            if outcome == "Over":
                gap = cons_pt - mb_pt                 # lower total at MyBookie favors the Over
            else:
                gap = mb_pt - cons_pt                 # higher total favors the Under
            side = outcome
            key_cross = False

        cents = (mb_price - cons_price) if (mb_price is not None and cons_price is not None and abs(gap) < 1e-9) else 0
        if gap >= config.ALERT_GAP_POINTS or key_cross or cents >= config.ALERT_GAP_CENTS:
            out.append({
                "event_id": event_id, "market": market, "side": side,
                "mb_point": mb_pt, "mb_price": mb_price, "cons_point": cons_pt, "cons_price": cons_price,
                "cons_books": n, "gap_points": round(gap, 2), "gap_cents": cents, "key_cross": key_cross,
                "home_team": game.get("home_team"), "away_team": game.get("away_team"),
                "commence_time": game.get("commence_time"), "week": game.get("week"),
            })
    return out


def filter_cooldown(candidates, recent_alerts):
    """Drop candidates already alerted within the cooldown unless the gap grew."""
    cutoff = now_utc() - timedelta(hours=config.ALERT_COOLDOWN_HOURS)
    best = {}
    for a in recent_alerts:
        ts = parse_ts(a["sent_at"])
        if ts and ts >= cutoff:
            k = (a["event_id"], a["market"], a["side"])
            prev = best.get(k)
            score = (float(a.get("gap_points") or 0), int(a.get("gap_cents") or 0))
            if prev is None or score > prev:
                best[k] = score
    out = []
    for c in candidates:
        k = (c["event_id"], c["market"], c["side"])
        score = (c["gap_points"], c["gap_cents"])
        if k not in best or score > best[k]:
            out.append(c)
    return out


def _fmt_pt(p):
    return f"{p:+g}" if p is not None else "?"


def _fmt_price(p):
    return f"({p:+d})" if p is not None else ""


_REASON_TEXT = {
    "close": "closing-line check before kickoff",
    "pre": "pre-kickoff check (inactives window)",
    "scheduled:monday_open": "scheduled Monday morning check",
    "scheduled:friday_reports": "scheduled Friday afternoon check",
    "manual": "manual check",
}


def why_checked(reason):
    """The pull reason in plain words. A news reason names the injury change that paid for the pull; it applies to
    every alert from that pull, whichever game it is (Ryan found 'Trigger: <player>' confusing, 2026-10-14)."""
    if not reason:
        return "?"
    if reason.startswith("news: "):
        return "injury news, " + reason[len("news: "):]
    return _REASON_TEXT.get(reason, reason)


def build_message(c, reason):
    ct = parse_ts(c.get("commence_time"))
    when = ct.astimezone(DENVER).strftime("%a %b %d %-I:%M %p MT") if ct else "?"
    matchup = f"{c.get('away_team')} @ {c.get('home_team')}"
    if c["market"] == "spreads":
        line = f"{c['side']} {_fmt_pt(c['mb_point'])} {_fmt_price(c['mb_price'])} at MyBookie vs consensus {_fmt_pt(c['cons_point'])} {_fmt_price(c['cons_price'])}"
    else:
        line = f"{c['side']} {c['mb_point']:g} {_fmt_price(c['mb_price'])} at MyBookie vs consensus {c['cons_point']:g} {_fmt_price(c['cons_price'])}"
    bits = []
    if c["gap_points"] >= config.ALERT_GAP_POINTS:
        bits.append(f"{c['gap_points']:g} pt better")
    if c["key_cross"]:
        bits.append("crosses key number")
    if c["gap_cents"] >= config.ALERT_GAP_CENTS:
        bits.append(f"{c['gap_cents']} cents better")
    title = f"Line gap: {matchup}"
    msg = (f"{line}\n"
           f"Edge: {', '.join(bits)} ({c['cons_books']} comparison books)\n"
           f"Kickoff: {when}\n"
           f"Checked because: {why_checked(reason)}\n"
           f"Captured: {now_utc().astimezone(DENVER).strftime('%-I:%M %p MT')}.\n"
           f"{c.get('model_text') or 'Model: no number for this game.'}")
    return title, msg


def with_alert_id(title, msg, alert_id):
    """Add the alert's number so Ryan can reply "A<id>" (or "A<id> 10" for $10) to log a bet on it. Applied after the
    row is inserted, because the id does not exist before that; the stored message is the version without it."""
    return f"A{alert_id} {title}", f"{msg}\nLog it: reply A{alert_id} (or A{alert_id} 10 for $10)"


def fingerprint(c):
    return f"{c['event_id']}|{c['market']}|{c['side']}|{c['mb_point']}|{c['cons_point']}|{c['mb_price']}"
