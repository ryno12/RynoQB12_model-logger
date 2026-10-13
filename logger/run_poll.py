"""The poller. Runs every 10 minutes from GitHub Actions (or by hand).

Each run:
  1. Polls ESPN injuries (free). Records new items and status changes.
  2. Decides whether to spend 2 credits on an odds pull, in priority order:
       close   - a tracked game kicks off within CLOSE_WINDOW_MIN and has no close pull yet
       pre     - a tracked game kicks off in 90-130 minutes (inactives window) and has no pre pull yet
       sched   - a scheduled window (Monday open, Friday reports) is due
       news    - an injury event met the trigger rules, cooldown and daily cap allow it, credits allow it
       manual  - FORCE_PULL=1
  3. If it pulled: upserts games, writes change-only snapshots, computes MyBookie-vs-consensus gaps,
     sends ntfy alerts (with cooldown), marks closes.
  4. Writes a runs row with credits used/remaining so the budget is visible.
"""
import sys
import traceback
from datetime import timedelta

from . import alerts as A
from . import grading
from . import model as M
from . import bets as B
from . import config, espn, ntfy
from .oddsapi import fetch_odds
from .snapshots import games_rows, snapshot_rows, split_started
from .store import iso, make_store, now_utc, parse_ts


def _due_scheduled(store, now):
    """Return the name of a scheduled pull that is due and not yet done for this week, else None."""
    for name, wd, hh, mm in config.SCHEDULED_PULLS:
        if now.weekday() != wd:
            continue
        target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if now < target:
            continue
        last = parse_ts(store.get_state(f"sched:{name}"))
        if last and last.date() == now.date():
            continue
        return name
    return None


def _close_due(games, now):
    win = timedelta(minutes=config.CLOSE_WINDOW_MIN)
    due = []
    for g in games:
        ct = parse_ts(g["commence_time"])
        if ct and not g.get("close_pull_at") and now <= ct <= now + win:
            due.append(g)
    return due


def _pre_due(games, now):
    lo = timedelta(minutes=config.PRE_WINDOW_MIN_LO)
    hi = timedelta(minutes=config.PRE_WINDOW_MIN_HI)
    due = []
    for g in games:
        ct = parse_ts(g["commence_time"])
        if ct and not g.get("pre_pull_at") and now + lo <= ct <= now + hi:
            due.append(g)
    return due


def _news_allowed(store, now, remaining):
    if remaining is not None and remaining < config.CREDIT_RESERVE_NEWS:
        return False, f"credits {remaining} < reserve {config.CREDIT_RESERVE_NEWS}"
    last = parse_ts(store.get_state("news:last_pull"))
    if last and now - last < timedelta(minutes=config.NEWS_COOLDOWN_MIN):
        return False, f"cooldown ({int((now - last).total_seconds() // 60)} min since last news pull)"
    day = now.strftime("%Y-%m-%d")
    cnt_day, cnt = (store.get_state("news:day_count") or "|0").split("|")
    if cnt_day == day and int(cnt) >= config.NEWS_DAILY_CAP:
        return False, f"daily cap {config.NEWS_DAILY_CAP} reached"
    return True, "ok"


def _bump_news(store, now):
    day = now.strftime("%Y-%m-%d")
    cnt_day, cnt = (store.get_state("news:day_count") or "|0").split("|")
    n = int(cnt) + 1 if cnt_day == day else 1
    store.set_state("news:day_count", f"{day}|{n}")
    store.set_state("news:last_pull", iso(now))


def main():
    store = make_store()
    now = now_utc()
    run_id = store.start_run("poll")
    summary = dict(pulled_odds=False, pull_reason="none", events_seen=0, snapshot_rows=0, injury_events=0, alerts_sent=0, notes="")
    notes = []
    try:
        # ---- 1. injuries (free) ----
        trigger_events = []
        try:
            payload = espn.fetch_injuries()
            items = espn.normalize(payload)
            prev_rows = store.injury_items()
            prev = {r["espn_item_id"]: r for r in prev_rows}
            first_run = len(prev) == 0
            events, upserts = espn.diff_items(prev, items, iso(now))
            store.upsert("injury_items", upserts)
            for e in events:
                e["run_id"] = run_id
                e["triggered_pull"] = espn.should_trigger(e, first_run=first_run)
            trigger_events = [e for e in events if e["triggered_pull"]]
            summary["injury_events"] = len(events)
            notes.append(f"espn: {len(items)} items, {len(events)} events, {len(trigger_events)} triggers" + (" (cold start, no triggers)" if first_run else ""))
            # insert events after trigger flags are set
            store.insert("injury_events", events)
        except Exception as ex:  # ESPN failing must not stop odds work
            notes.append(f"espn FAILED: {ex}")

        # ---- 1b. bets posted to the ntfy topic (free) ----
        try:
            n_bets = B.process(store, now, run_id)
            if n_bets:
                notes.append(f"bets: {n_bets} logged from ntfy")
        except Exception as ex:
            notes.append(f"bets FAILED: {ex}")

        # ---- 1c. grading: finals from ESPN (free) -> bets, alerts, closing-line value ----
        try:
            g_note = grading.run(store, now)
            if g_note:
                notes.append(g_note)
        except Exception as ex:
            notes.append(f"grading FAILED: {ex}")

        # ---- 2. decide on an odds pull ----
        games = store.upcoming_games()

        # ---- 1d. new model run? push the biggest disagreements with MyBookie once (free) ----
        try:
            m_note = M.notify_if_new(store, games, now, lambda t, m: ntfy.send(t, m, priority="low", tags="crystal_ball"), config.TARGET_BOOK)
            if m_note:
                notes.append(m_note)
        except Exception as ex:
            notes.append(f"model push FAILED: {ex}")
        remaining_known = store.get_state("credits:remaining")
        remaining = int(remaining_known) if remaining_known not in (None, "") else None
        reason = None
        closes = _close_due(games, now)
        pres = _pre_due(games, now)
        sched = _due_scheduled(store, now)
        if config.FORCE_PULL:
            reason = "manual"
        elif closes:
            reason = "close"
        elif pres:
            reason = "pre"
        elif sched:
            if remaining is not None and remaining < config.CREDIT_RESERVE_SCHED:
                notes.append(f"scheduled {sched} skipped: credits {remaining} < reserve {config.CREDIT_RESERVE_SCHED}")
            else:
                reason = f"scheduled:{sched}"
        elif trigger_events:
            ok, why = _news_allowed(store, now, remaining)
            if ok:
                reason = "news"
            else:
                notes.append(f"news pull skipped: {why}")

        # ---- 3. pull and process ----
        if reason:
            res = fetch_odds()
            events, started = split_started(res.events, now)
            if started:
                notes.append(f"{len(started)} in-play events ignored")
            if res.credits_remaining is not None:
                store.set_state("credits:remaining", res.credits_remaining)
            summary.update(pulled_odds=True, pull_reason=reason, events_seen=len(events),
                           credits_used=res.credits_used, credits_remaining=res.credits_remaining)

            seasons = store.seasons()
            existing = {g["id"]: g for g in games}
            g_rows = games_rows(events, seasons, existing)
            store.upsert("games", g_rows)
            games_by_id = {g["id"]: g for g in g_rows}

            latest = store.latest_quotes([e["id"] for e in events])
            snaps, upserts = snapshot_rows(events, latest, run_id)
            store.insert("odds_snapshots", snaps)
            store.upsert("odds_latest", upserts)
            summary["snapshot_rows"] = len(snaps)

            if sched:  # any pull that happens while a scheduled window is due satisfies that window
                store.set_state(f"sched:{sched}", iso(now))
            if reason == "news":
                _bump_news(store, now)
            if closes:
                for g in closes:
                    store.patch("games", {"id": f"eq.{g['id']}"}, {"close_pull_at": iso(now)})
                notes.append("close pulled for: " + "; ".join(f"{g['away_team']} @ {g['home_team']}" for g in closes))
            if pres:  # any pull inside a game's pre window counts as its pre pull
                for g in pres:
                    store.patch("games", {"id": f"eq.{g['id']}"}, {"pre_pull_at": iso(now)})
                notes.append("pre-kickoff pulled for: " + "; ".join(f"{g['away_team']} @ {g['home_team']}" for g in pres))

            # ---- alerts ----
            trig_txt = reason
            if reason == "news" and trigger_events:
                e = trigger_events[0]
                trig_txt = f"news: {e.get('athlete_name')} ({e.get('team')} {e.get('position')}) {e.get('old_status') or 'new'} -> {e.get('new_status')}"
                if len(trigger_events) > 1:
                    trig_txt += f" (+{len(trigger_events) - 1} more)"
            cands = A.find_gaps(upserts, games_by_id)
            recent = store.recent_alerts(now - timedelta(hours=config.ALERT_COOLDOWN_HOURS))
            cands = A.filter_cooldown(cands, recent)
            try:
                M.annotate(cands, store.model_lines_open(), games_by_id)
            except Exception as ex:   # the model is optional; never block an alert on it
                notes.append(f"model lookup FAILED: {ex}")
            # Insert first, then push: the push carries the row's id ("A12") so a reply can reference it.
            pushes, sent_rows = [], []
            for c in sorted(cands, key=lambda c: (-c["gap_points"], -c["gap_cents"])):
                title, msg = A.build_message(c, trig_txt)
                pushes.append((title, msg))
                sent_rows.append({
                    "fingerprint": A.fingerprint(c), "event_id": c["event_id"], "market": c["market"], "side": c["side"],
                    "mb_point": c["mb_point"], "mb_price": c["mb_price"], "cons_point": c["cons_point"], "cons_price": c["cons_price"],
                    "cons_books": c["cons_books"], "gap_points": c["gap_points"], "gap_cents": c["gap_cents"], "key_cross": c["key_cross"],
                    "reason": trig_txt, "message": msg, "sent_at": iso(now_utc()), "run_id": run_id,
                    "model_point": c.get("model_point"), "model_edge": c.get("model_edge"), "model_p": c.get("model_p"),
                })
            inserted = (store.insert("alerts", sent_rows, returning=True) or []) if sent_rows else []
            for (title, msg), row in zip(pushes, inserted):
                ntfy.send(*A.with_alert_id(title, msg, row["id"]))
            summary["alerts_sent"] = len(sent_rows)
            notes.append(f"odds: {len(events)} events, {len(snaps)} changed quotes, {len(sent_rows)} alerts, credits remaining {res.credits_remaining}")
        else:
            notes.append("no odds pull this run")

        summary["notes"] = " | ".join(notes)
        store.finish_run(run_id, **summary)
        print(summary["notes"])
        return 0
    except Exception:
        tb = traceback.format_exc()
        summary["notes"] = (" | ".join(notes) + " | FAILED: " + tb)[-4000:]
        try:
            store.finish_run(run_id, **summary)
        finally:
            print(tb, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
