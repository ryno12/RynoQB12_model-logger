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


def _edge_fixture(mb_home_pt, cons_home_pt, adj_spread, mb_price=-110):
    from logger import model as M
    g = {"id": "e", "week": 10, "home_team": "Cleveland Browns", "away_team": "Houston Texans", "commence_time": "2030-11-15T18:00:00Z"}
    row = {"week": 10, "home_team": g["home_team"], "away_team": g["away_team"], "fair_spread": 99.0, "fair_total": 40.0,
           "shrink_spread": 1.0, "sigma_spread": 13.0, "shrink_total": 0.0, "sigma_total": 13.0,
           "inputs": {"adj_spread": adj_spread, "adj_total": 0.0}}
    q = [{"event_id": "e", "market": "spreads", "outcome": g["home_team"], "bookmaker": "mybookieag", "point": mb_home_pt, "price": mb_price},
         {"event_id": "e", "market": "spreads", "outcome": g["away_team"], "bookmaker": "mybookieag", "point": -mb_home_pt, "price": mb_price}]
    q += [{"event_id": "e", "market": "spreads", "outcome": o, "bookmaker": b, "point": p, "price": -110}
          for b in ("draftkings", "fanduel") for o, p in ((g["home_team"], cons_home_pt), (g["away_team"], -cons_home_pt))]
    return M.edges([row], [g], q, "mybookieag", {"draftkings", "fanduel"}, 2, 0.02), M


def test_model_edge_reanchors_to_current_consensus():
    # stored fair_spread (99) is stale; the edge must come from consensus (-3 home pt) + adj (+3 home margin) = home by 6
    found, M = _edge_fixture(mb_home_pt=-3, cons_home_pt=-3, adj_spread=3.0)
    assert [(e["side"], e["model_point"]) for e in found] == [("Cleveland Browns", -6.0)]
    assert abs(found[0]["edge"] - (M.phi(3 / 13) - 110 / 210)) < 1e-3
    title, msg = M.edge_message(found[0], __import__("zoneinfo").ZoneInfo("America/Denver"))
    assert title == "Model edge: Texans @ Browns" and "Browns -3 (-110) at MyBookie" in msg


def test_model_edge_none_when_small():
    found, _ = _edge_fixture(mb_home_pt=-3, cons_home_pt=-3, adj_spread=0.5)
    assert found == []
