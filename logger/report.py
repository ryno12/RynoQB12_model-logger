"""Weekly report: a markdown file under reports/<season>/ plus a short ntfy summary.

Runs on Tuesdays from dump.yml after the Parquet backup (or by hand: `python -m logger.report`, add `--dump DIR` to
build it from a Parquet dump instead of Supabase, `--week N` to pick a week, `--no-push` to skip ntfy).
Reports the week that just ended (current NFL week minus one, Tuesday-to-Monday in New York time):

  bets     record, dollars, units (profit / UNIT_DOLLARS), CLV vs consensus close and vs MyBookie close, per-bet table,
           season to date, bankroll ($300 start) and the frozen pause rules ($180, or negative CLV after 75 graded)
  alerts   sent, voided (in-play), graded as $5 flat bets, share that moved toward consensus by close, by market,
           repeat rate (same fingerprint re-alerted after the cooldown)
  logger   polls, failures, odds pulls by reason, credits used this week and remaining, close/pre pulls captured
"""
import argparse
import sys
from collections import Counter
from datetime import timedelta
from pathlib import Path
from statistics import mean

from . import config, ntfy
from .store import iso, make_store, now_utc, parse_ts
from .weeks import ny_date, season_week

BANKROLL_START = 300.0
PAUSE_BANKROLL = 180.0
PAUSE_CLV_AFTER = 75
GRADED = {"won", "lost", "push", "void"}


def _f(x):
    try:
        return None if x is None or x != x else float(x)   # x != x catches NaN from Parquet
    except (TypeError, ValueError):
        return None


def _avg(xs):
    xs = [x for x in xs if x is not None]
    return mean(xs) if xs else None


def _money(x):
    return "n/a" if x is None else (f"+${x:.2f}" if x >= 0 else f"-${-x:.2f}")


def _signed(x, nd=2, unit=""):
    return "n/a" if x is None else f"{x:+.{nd}f}{unit}"


def _pt(x):
    """Format a line: +3.5 / -3.5 / 47.5 (totals have no sign)."""
    x = _f(x)
    return "n/a" if x is None else (f"{x:+g}" if x < 0 or x > 0 and x < 20 else f"{x:g}")


def record(rows, key):
    c = Counter(r.get(key) for r in rows)
    return f"{c['won']}-{c['lost']}-{c['push']}" + (f" ({c['void']} void)" if c["void"] else "")


# ---------------------------------------------------------------------------------------------------------------------
# data

def load_store(store, since):
    games = store.select_all("games", {"select": "*"}, order="id")
    bets = store.select_all("bets", {"select": "*"}, order="id")
    alerts = store.select_all("alerts", {"select": "*"}, order="id")
    runs = store.select_all("runs", {"select": "*", "started_at": f"gte.{iso(since)}"}, order="id")
    return dict(games=games, bets=bets, alerts=alerts, runs=runs, seasons=store.seasons())


def load_dump(d):
    import pyarrow.parquet as pq
    from datetime import date
    t = {n: pq.read_table(Path(d) / f"{n}.parquet").to_pylist() for n in ["games", "bets", "alerts", "runs", "seasons"]}
    t["seasons"] = {int(r["year"]): date.fromisoformat(str(r["week1_tuesday"])) for r in t["seasons"]} or dict(config.SEASONS_FALLBACK)
    for rows in (t["games"], t["bets"], t["alerts"], t["runs"]):
        for r in rows:
            for k, v in r.items():
                if hasattr(v, "isoformat"):
                    r[k] = iso(v)
    return t


# ---------------------------------------------------------------------------------------------------------------------
# stats

def bet_stats(bets):
    g = [b for b in bets if b.get("status") in GRADED]
    scored = [b for b in g if b.get("status") != "void"]
    profit = sum(_f(b.get("profit")) or 0 for b in g)
    return dict(
        n=len(g), record=record(g, "status"), profit=profit, units=profit / config.UNIT_DOLLARS,
        clv_points=_avg(_f(b.get("clv_points")) for b in scored),
        clv_cents=_avg(_f(b.get("clv_cents")) for b in scored if _f(b.get("clv_points")) == 0),
        clv_mb_points=_avg(_f(b.get("clv_mb_points")) for b in scored),
        clv_known=sum(1 for b in scored if _f(b.get("clv_points")) is not None),
    )


def alert_stats(alerts):
    graded = [a for a in alerts if a.get("graded_at")]
    void = [a for a in graded if a.get("result") == "void"]
    scored = [a for a in graded if a.get("result") in ("won", "lost", "push")]
    moved = [a.get("moved_toward_consensus") for a in scored if a.get("moved_toward_consensus") is not None]
    fps = Counter(a.get("fingerprint") for a in alerts)
    by_market = {}
    for m in ("spreads", "totals"):
        s = [a for a in scored if a.get("market") == m]
        if s:
            by_market[m] = dict(record=record(s, "result"), profit=sum(_f(a.get("profit")) or 0 for a in s),
                                moved=_avg(1.0 if x else 0.0 for x in (a.get("moved_toward_consensus") for a in s) if x is not None))
    return dict(
        sent=len(alerts), graded=len(graded), void=len(void), scored=len(scored), ungraded=len(alerts) - len(graded),
        record=record(scored, "result"), profit=sum(_f(a.get("profit")) or 0 for a in scored),
        moved=(sum(1 for x in moved if x) / len(moved)) if moved else None, moved_n=len(moved),
        close_gap=_avg(_f(a.get("close_gap_points")) for a in scored),
        alert_gap=_avg(_f(a.get("gap_points")) for a in scored),
        key_cross=sum(1 for a in alerts if a.get("key_cross")),
        distinct=len(fps), repeats=sum(n - 1 for n in fps.values()),
        by_market=by_market,
    )


def run_stats(runs, games_in_week):
    polls = [r for r in runs if r.get("run_kind") == "poll"]
    failed = [r for r in polls if "FAILED" in (r.get("notes") or "")]
    pulls = [r for r in polls if r.get("pulled_odds")]
    reasons = Counter((r.get("pull_reason") or "").split(":")[0] for r in pulls)
    rem = [_f(r.get("credits_remaining")) for r in pulls if _f(r.get("credits_remaining")) is not None]
    last_rem = rem[-1] if rem else None
    # runs.credits_used is the API's running monthly total, so count the drops in credits_remaining between pulls
    # (the first pull in the window is assumed to cost one pull's worth, 2 credits).
    used = (2 if rem else 0) + sum(max(0.0, a - b) for a, b in zip(rem, rem[1:]))
    fails = Counter()
    for r in failed:
        for seg in (r.get("notes") or "").split(" | "):
            if "FAILED" in seg:
                fails[seg[:120]] += 1
    return dict(
        polls=len(polls), failed=len(failed), pulls=len(pulls), reasons=dict(reasons), used=used, remaining=last_rem,
        closes=sum(1 for g in games_in_week if g.get("close_pull_at")), pres=sum(1 for g in games_in_week if g.get("pre_pull_at")),
        games=len(games_in_week), failures=fails.most_common(4),
    )


# ---------------------------------------------------------------------------------------------------------------------
# report

def build(data, season, week, now):
    games = {g["id"]: g for g in data["games"]}
    wk_games = [g for g in data["games"] if g.get("season") == season and g.get("week") == week]
    wk_ids = {g["id"] for g in wk_games}
    bets_all, alerts_all = data["bets"], data["alerts"]
    wk_bets = [b for b in bets_all if b.get("event_id") in wk_ids]
    wk_alerts = [a for a in alerts_all if a.get("event_id") in wk_ids]
    runs = [r for r in data["runs"] if parse_ts(r.get("started_at")) and parse_ts(r["started_at"]) >= now - timedelta(days=7)]

    bw, bs = bet_stats(wk_bets), bet_stats([b for b in bets_all if b.get("status") in GRADED])
    aw, as_ = alert_stats(wk_alerts), alert_stats(alerts_all)
    rs = run_stats(runs, wk_games)
    bankroll = BANKROLL_START + bs["profit"]
    open_bets = [b for b in bets_all if b.get("status") == "open"]
    review = [b for b in bets_all if b.get("status") == "needs_review"]
    pause = []
    if bankroll <= PAUSE_BANKROLL:
        pause.append(f"bankroll ${bankroll:.2f} is at or below the ${PAUSE_BANKROLL:.0f} stop")
    if bs["n"] >= PAUSE_CLV_AFTER and bs["clv_points"] is not None and bs["clv_points"] < 0:
        pause.append(f"average CLV {bs['clv_points']:+.2f} after {bs['n']} graded bets")

    kicks = sorted(ny_date(parse_ts(g["commence_time"])) for g in wk_games if g.get("commence_time"))
    span = f"{kicks[0]:%b %d} to {kicks[-1]:%b %d}" if kicks else "no games found"
    finals = sum(1 for g in wk_games if g.get("espn_status") == "STATUS_FINAL")

    L = []
    L.append(f"# Week {week} report ({season})")
    L.append("")
    L.append(f"Games {span}, {finals} of {len(wk_games)} final. Generated {now:%Y-%m-%d %H:%M} UTC.")
    L.append("")
    L.append("## Bets")
    L.append("")
    L.append(f"- Week: **{bw['record']}**, {_money(bw['profit'])} ({_signed(bw['units'], 2, 'u')}), "
             f"CLV {_signed(bw['clv_points'])} pts vs consensus close, {_signed(bw['clv_mb_points'])} vs MyBookie close"
             f" ({bw['clv_known']} of {bw['n']} with a close).")
    L.append(f"- Season: **{bs['record']}**, {_money(bs['profit'])} ({_signed(bs['units'], 2, 'u')}), "
             f"CLV {_signed(bs['clv_points'])} pts, bankroll **${bankroll:.2f}** of ${BANKROLL_START:.0f}.")
    L.append(f"- Pause rules: {'**PAUSE: ' + '; '.join(pause) + '**' if pause else 'not triggered'} "
             f"(stop at ${PAUSE_BANKROLL:.0f} or negative CLV after {PAUSE_CLV_AFTER} graded).")
    if review:
        L.append(f"- {len(review)} bet message(s) still unmatched (needs_review): " + "; ".join(f"#{b['id']} \"{b.get('raw_text')}\"" for b in review))
    if wk_bets:
        L.append("")
        L.append("| # | Game | Bet | Price | Result | P/L | Close cons / MB | CLV pts | vs MB |")
        L.append("|---|---|---|---|---|---|---|---|---|")
        for b in sorted(wk_bets, key=lambda b: b["id"]):
            g = games.get(b.get("event_id"), {})
            side = b.get("side") if b.get("market") == "spreads" else f"{b.get('side')} {b.get('line')}"
            bet = f"{side} {_pt(b.get('line'))}" if b.get("market") == "spreads" else side
            close = f"{_pt(b.get('close_cons_point'))} / {_pt(b.get('close_mb_point'))}" if b.get("close_seen_at") else "none"
            L.append(f"| {b['id']} | {g.get('away_team', '?')} @ {g.get('home_team', '?')} | {bet} | {b.get('price')} | {b.get('status')} | "
                     f"{_money(_f(b.get('profit')))} | {close} | {_signed(_f(b.get('clv_points')))} | {_signed(_f(b.get('clv_mb_points')))} |")
    if open_bets:
        L.append("")
        L.append("Open: " + "; ".join(f"#{b['id']} {b.get('side')} {_pt(b.get('line')) if b.get('market') == 'spreads' else b.get('line')} ({b.get('price')}) "
                                      f"{games.get(b.get('event_id'), {}).get('away_team', '?')} @ {games.get(b.get('event_id'), {}).get('home_team', '?')}"
                                      for b in open_bets))
    L.append("")
    L.append("## Alerts (MyBookie vs consensus gaps, scored as $5 flat bets at the alerted line)")
    L.append("")
    L.append(f"- Week: {aw['sent']} sent, {aw['void']} voided (in-play line), {aw['ungraded']} ungraded. "
             f"Pre-game alerts **{aw['record']}**, {_money(aw['profit'])}; "
             f"{'n/a' if aw['moved'] is None else f'{aw['moved']:.0%}'} moved toward consensus by close "
             f"(gap {_signed(aw['alert_gap'])} at alert, {_signed(aw['close_gap'])} at close); "
             f"{aw['distinct']} distinct lines, {aw['repeats']} repeats, {aw['key_cross']} key-number crosses.")
    for m, s in aw["by_market"].items():
        L.append(f"  - {m}: {s['record']}, {_money(s['profit'])}, {'n/a' if s['moved'] is None else f'{s['moved']:.0%}'} moved toward consensus")
    L.append(f"- Season: {as_['sent']} sent, {as_['void']} voided, pre-game **{as_['record']}**, {_money(as_['profit'])}, "
             f"{'n/a' if as_['moved'] is None else f'{as_['moved']:.0%}'} moved toward consensus ({as_['moved_n']} graded).")
    L.append("")
    L.append("## Logger (last 7 days)")
    L.append("")
    L.append(f"- {rs['polls']} polls, {rs['failed']} with a failure; {rs['pulls']} odds pulls "
             f"({', '.join(f'{k} {v}' for k, v in sorted(rs['reasons'].items())) or 'none'}); "
             f"{rs['used']:.0f} credits used, **{'n/a' if rs['remaining'] is None else int(rs['remaining'])} remaining**.")
    L.append(f"- Week {week} games with a pre-kickoff pull: {rs['pres']} of {rs['games']}; with a close pull: {rs['closes']} of {rs['games']}.")
    for note, n in rs["failures"]:
        L.append(f"  - {n}x `{note}`")
    md = "\n".join(L) + "\n"

    title = f"Week {week} report"
    msg = (f"Bets {bw['record']}, {_money(bw['profit'])}, CLV {_signed(bw['clv_points'])} | "
           f"Season {bs['record']}, bank ${bankroll:.0f}"
           + (f" | PAUSE: {'; '.join(pause)}" if pause else "") +
           f" | Alerts {aw['record']}, {_money(aw['profit'])}, "
           f"{'n/a' if aw['moved'] is None else f'{aw['moved']:.0%}'} to close, {aw['void']} in-play voided | "
           f"Credits {'n/a' if rs['remaining'] is None else int(rs['remaining'])} ({rs['used']:.0f} used), {rs['polls']} polls, {rs['failed']} failed")
    return md, title, msg


def report_week(now, seasons):
    season, week = season_week(now, seasons)
    return season, week - 1


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", help="build from a Parquet dump directory instead of Supabase")
    ap.add_argument("--week", type=int, help="NFL week to report (default: the week that just ended)")
    ap.add_argument("--no-push", action="store_true", help="do not send the ntfy summary")
    ap.add_argument("--out", default="reports", help="output directory (default reports/)")
    ap.add_argument("--force", action="store_true", help="rebuild and re-send even if this week's report already exists")
    a = ap.parse_args(argv)
    now = now_utc()
    data = load_dump(a.dump) if a.dump else load_store(make_store(), now - timedelta(days=8))
    season, week = report_week(now, data["seasons"])
    if a.week:
        week = a.week
    out = Path(a.out) / str(season) / f"week-{week:02d}.md"
    if out.exists() and not a.force:
        # The Tuesday cron can fire hours late and land after a manual dispatch; one report per week, not two.
        print(f"{out} already exists; nothing to do (use --force to rebuild)")
        return 0
    md, title, msg = build(data, season, week, now)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md)
    print(md)
    print(f"wrote {out}")
    if not a.no_push:
        ntfy.send(title, msg, priority="default", tags="bar_chart")
    return 0


if __name__ == "__main__":
    sys.exit(main())
