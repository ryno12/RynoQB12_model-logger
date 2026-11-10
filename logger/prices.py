"""American-price arithmetic shared by alerts and grading (2026-11-10).

American prices jump from -100 to +100, so plain subtraction and plain medians break across even money: +100 vs -105
read as 205 cents (it is 5), and the median of +100 and -105 came out as -2. 101 of the first 370 alerts fired on that
(MyBookie +100 vs consensus about -105, a real gap under the 10-cent rule).
"""
from statistics import median


def implied(a) -> float:
    a = float(a)
    return -a / (-a + 100) if a < 0 else 100 / (a + 100)


def american(p: float) -> int:
    return int(round(-100 * p / (1 - p))) if p > 0.5 else int(round(100 * (1 - p) / p))


def median_price(prices):
    """Median of American prices, taken in implied chance."""
    prices = [x for x in prices if x is not None]
    return american(median(implied(x) for x in prices)) if prices else None


def cents(a) -> int:
    """A price on one straight scale (+100 sits next to -100), for differences: cents(-105) - cents(100) == -5."""
    a = int(a)
    return a if a < 0 else a - 200
