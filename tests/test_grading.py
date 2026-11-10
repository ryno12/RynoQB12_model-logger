from logger import grading as G
from logger import prices as P


def test_median_price_across_even():
    assert P.median_price([100, -105]) in (-102, -103)
    assert P.median_price([-110, -110, -120]) == -110
    assert P.median_price([+120, +130, +140]) == 130
    assert P.median_price([]) is None


def test_clv_cents_across_even():
    assert G.clv("spreads", "X", 3, -105, 3, 100) == (0.0, -5)
    assert G.clv("spreads", "X", -3, -115, -3, -125) == (0.0, 10)
    assert G.clv("spreads", "X", 3, 110, 3, -110) == (0.0, 20)


def test_alert_ignores_plus100_vs_minus105():
    from logger import alerts as A
    g = {"e": {"home_team": "H", "away_team": "V", "commence_time": "2030-01-01T00:00:00Z", "week": 1}}
    q = [{"event_id": "e", "market": "spreads", "outcome": "H", "bookmaker": b, "point": -3, "price": p}
         for b, p in [("mybookieag", 100)] + [(b, -105) for b in sorted(__import__("logger.config", fromlist=["x"]).CONSENSUS_BOOKS)]]
    assert A.find_gaps(q, g) == []
    q[0]["price"] = 105
    assert [c["gap_cents"] for c in A.find_gaps(q, g)] == [10]
