"""NFL season/week math. Mirrors the pick'em: a week runs Tuesday to Monday, dated in America/New_York."""
from datetime import datetime, date, timezone
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")


def ny_date(ts: datetime) -> date:
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(NY).date()


def season_week(ts: datetime, seasons: dict) -> tuple[int, int]:
    """seasons: {year: week1_tuesday(date)}. Returns (season, week)."""
    d = ny_date(ts)
    candidates = [(y, t) for y, t in seasons.items() if t <= d]
    if not candidates:
        raise ValueError(f"No season configured covering {d}")
    year, tuesday = max(candidates, key=lambda x: x[1])
    return year, (d - tuesday).days // 7 + 1
