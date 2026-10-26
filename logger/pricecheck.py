"""Price check (2026-10-26): Ryan texts a number he sees at any book (Underdog Predict, MyBookie, anything) and the
poller replies whether it is a good price. No new data, no credits: it uses the latest quotes already stored.

    check Chiefs -3.5 52        spread, contract price 52 cents (prediction market, e.g. Underdog Predict)
    check Bears over 44.5 48c   total, 48 cents
    check Chiefs -3.5 -115      American odds
    check Chiefs -3.5           no price: just the fair chance and the break-even price

Fair chance: each consensus book's two-way price at its own number, juice removed, turned into an expected margin
(or total), then read off at the number asked about; the median across books. Spreads use a normal margin (sigma ~13,
the model's own sigma when it has one) reweighted by how often each final margin really happens (KEY_W, NFL
2015-2025: 14.6% of games end on exactly 3, 8.7% on 7), so the half point across 3 is worth what it is. Pushes are
treated as refunds: the chance shown is win / (win + loss). Totals use the plain normal (their key numbers are weak).
Cost: cents / 100 plus config.PRICE_CHECK_FEE_CENTS, or the American price's implied chance.
"""
import re
from statistics import NormalDist, median

from . import bets as B
from . import config
from . import model as M
from .store import parse_ts

N = NormalDist()
CHECK_RE = re.compile(r"^\s*(?:check|price\s*check|pc)\b[:\s]*", re.I)
AMERICAN_END = re.compile(r"(?<![\d.])([+-]\d{3,4})\s*$")
CENTS_END = re.compile(r"(?<![\d.+-])(0?\.\d+|\d{1,2}(?:\.\d+)?)\s*(?:c|¢|cents?)?\s*$", re.I)
SIGMA = 13.0
# Final-margin frequency / its own smoothed level, |margin| 0..24, NFL regular seasons 2015-2025 (2,895 games).
KEY_W = {0: 0.07, 1: 0.82, 2: 0.79, 3: 2.4, 4: 0.76, 5: 0.73, 6: 1.21, 7: 1.63, 8: 0.89, 9: 0.4, 10: 1.27, 11: 0.61,
         12: 0.58, 13: 0.69, 14: 1.84, 15: 0.65, 16: 0.92, 17: 1.45, 18: 0.98, 19: 0.55, 20: 1.07, 21: 1.26, 22: 0.62,
         23: 0.88, 24: 1.38}
MARGINS = range(-70, 71)


def _pmf(mu, sigma):
    w = [N.pdf((m - mu) / sigma) * KEY_W.get(abs(m), 1.0) for m in MARGINS]
    t = sum(w)
    return [x / t for x in w]


def cover_prob(mu, sigma, point):
    """P(side covers | not a push) when the side's margin has mean mu and it gets `point`."""
    pm = _pmf(mu, sigma)
    win = sum(p for m, p in zip(MARGINS, pm) if m + point > 0)
    loss = sum(p for m, p in zip(MARGINS, pm) if m + point < 0)
    return win / (win + loss)


def solve_mu(q, sigma, point):
    """The mean margin at which cover_prob(mu, sigma, point) == q (bisection; cover_prob rises with mu)."""
    lo, hi = -40.0, 40.0
    for _ in range(50):
        mid = (lo + hi) / 2
        if cover_prob(mid, sigma, point) < q:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2
GOOD, BAD = 0.02, -0.02


def is_check(text: str) -> bool:
    return bool(CHECK_RE.match(text or ""))


def implied(american: int) -> float:
    a = float(american)
    return -a / (-a + 100) if a < 0 else 100 / (a + 100)


def parse_check(text: str) -> dict:
    """{side, market, line, teams, cost (0..1) or None, price_txt}."""
    body = CHECK_RE.sub("", text, count=1).strip()
    cost, price_txt = None, None
    m = AMERICAN_END.search(body)
    if m:
        cost, price_txt = implied(int(m.group(1))), m.group(1)
        body = body[:m.start()]
    else:
        m = CENTS_END.search(body)
        # a bare number at the end is a price only if something else (the line) is still left before it
        rest = B._strip_teams(body[:m.start()]) if m else ""
        if m and re.search(r"\d|\bpk\b", rest, re.I):
            v = float(m.group(1))
            c = v if v < 1 else v / 100
            if 0 < c < 1:
                cost, price_txt = c + config.PRICE_CHECK_FEE_CENTS / 100, f"{c * 100:g}c"
                body = body[:m.start()]
    p = B.parse(body)
    p.update(cost=cost, price_txt=price_txt)
    return p


def _book_quotes(quotes, game, market, side):
    """{book: (point for `side`, price side, price other)} for one market."""
    by = {}
    for q in quotes:
        if q["market"] != market or q.get("point") is None:
            continue
        by.setdefault(q["bookmaker"], {})[q["outcome"]] = q
    out = {}
    other = {game["home_team"]: game["away_team"], game["away_team"]: game["home_team"], "Over": "Under", "Under": "Over"}[side]
    for book, d in by.items():
        if side in d and other in d and d[side].get("price") and d[other].get("price"):
            out[book] = (float(d[side]["point"]), int(d[side]["price"]), int(d[other]["price"]))
    return out


def fair_prob(quotes, game, market, side, point, sigma=SIGMA):
    """Median consensus fair chance that `side` wins at `point`. None without 2+ consensus books."""
    probs = []
    for book, (pt, pr_s, pr_o) in _book_quotes(quotes, game, market, side).items():
        if book not in config.CONSENSUS_BOOKS:
            continue
        a, b = implied(pr_s), implied(pr_o)
        q = min(max(a / (a + b), 0.01), 0.99)
        if market == "spreads":
            probs.append(cover_prob(solve_mu(q, sigma, pt), sigma, point))
            continue
        z = N.inv_cdf(q)
        # totals: Over wins when total > pt, Under when total < pt; move the threshold by the point difference
        shift = (point - pt) if side == "Under" else (pt - point)
        probs.append(N.cdf(z + shift / sigma))
    return median(probs) if len(probs) >= config.MIN_CONSENSUS_BOOKS else None


def _fmt_line(market, side, point):
    return f"{side} {point:+g}" if market == "spreads" else f"{side} {point:g}"


def _cents(p):
    return f"{p * 100:.1f}c"


def check(store, text, now):
    """Returns (title, message) for the reply."""
    p = parse_check(text)
    game = B.match_game(store, p, now)
    if not game or not p.get("side") or p.get("line") is None:
        return ("Price check: could not read it",
                f"\"{text[:80]}\". Try 'check Chiefs -3.5 52' (52 cents) or 'check Bears over 44.5 -110'.")
    market, side, point = p.get("market", "spreads"), p["side"], float(p["line"])
    quotes = store.latest_quotes([game["id"]])
    rows = store.model_lines_open()
    row = M.lookup(M.index(rows), game)
    sig = SIGMA
    if row:
        s = row.get("sigma_spread" if market == "spreads" else "sigma_total")
        sig = float(s) if s else SIGMA
    fair = fair_prob(quotes, game, market, side, point, sig)
    head = f"{_fmt_line(market, side, point)} ({game['away_team']} @ {game['home_team']})"
    if fair is None:
        return (f"Price check: {_fmt_line(market, side, point)}", f"{head}: no consensus prices stored for this game yet.")
    lines = []
    if p.get("cost") is not None:
        edge = fair - p["cost"]
        verdict = "GOOD price" if edge >= GOOD else "BAD price" if edge <= BAD else "About fair"
        lines.append(f"{verdict}: {p['price_txt']} costs {p['cost']:.1%}, fair chance {fair:.1%} ({edge:+.1%}).")
    else:
        lines.append(f"Fair chance {fair:.1%}.")
    lines.append(f"Break-even: pay {_cents(fair)} or less (American {_american(fair)} or better).")
    # MyBookie on the same side
    mb = _book_quotes(quotes, game, market, side).get(config.TARGET_BOOK)
    if mb:
        mb_fair = fair_prob(quotes, game, market, side, mb[0], sig)
        if mb_fair is not None:
            mb_edge = mb_fair - implied(mb[1])
            better = ""
            if p.get("cost") is not None:
                better = " MyBookie is the better bet." if mb_edge > fair - p["cost"] + 0.005 else \
                         " Your price is the better bet." if fair - p["cost"] > mb_edge + 0.005 else " About the same as yours."
            lines.append(f"MyBookie: {_fmt_line(market, side, mb[0])} ({mb[1]:+d}), {mb_edge:+.1%} vs fair.{better}")
    if row:
        v = M.view(row, market, side, point, game["home_team"])
        lines.append(M.text(v, market, side))
    seen = [parse_ts(q.get("seen_at")) for q in quotes if q.get("seen_at")]
    if seen:
        lines.append(f"Market as of {max(seen).astimezone(B.MT):%a %-I:%M %p} MT. Pushes treated as refunds.")
    return (f"Price check: {_fmt_line(market, side, point)}", "\n".join(lines))


def _american(p):
    p = min(max(p, 0.01), 0.99)
    return f"{-round(100 * p / (1 - p)):+d}" if p >= 0.5 else f"+{round(100 * (1 - p) / p):d}"
