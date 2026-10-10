"""Storage layer. SupabaseStore talks to PostgREST; LocalStore keeps JSON files for dry runs.
Both expose the same small set of operations the poller needs."""
import json
from datetime import datetime, timezone, date
from pathlib import Path

import requests

from . import config


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso(ts) -> str | None:
    if ts is None:
        return None
    if isinstance(ts, str):
        return ts
    return ts.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_ts(s):
    if not s:
        return None
    return datetime.fromisoformat(str(s).replace("Z", "+00:00"))


class SupabaseStore:
    def __init__(self, url=config.SUPABASE_URL, key=config.SUPABASE_SERVICE_ROLE_KEY):
        if not url or not key:
            raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required (or set DRY_RUN=1)")
        self.url = url
        self.key = key
        self.s = requests.Session()
        headers = {"apikey": key, "Content-Type": "application/json"}
        # Legacy service_role keys are JWTs and also go in Authorization; new sb_secret_ keys only need apikey.
        if key.startswith("eyJ"):
            headers["Authorization"] = f"Bearer {key}"
        self.s.headers.update(headers)

    def _req(self, method, path, body=None, prefer=None, params=None):
        headers = {"Prefer": prefer} if prefer else {}
        r = self.s.request(method, f"{self.url}/rest/v1/{path}", json=body, headers=headers, params=params, timeout=60)
        if r.status_code >= 300:
            raise RuntimeError(f"Supabase {method} {path} -> {r.status_code}: {r.text[:500]}")
        return r.json() if r.text else None

    # -- generic
    def select(self, table, params):
        return self._req("GET", table, params=params) or []

    def upsert(self, table, rows, on_conflict=None):
        if not rows:
            return
        params = {"on_conflict": on_conflict} if on_conflict else None
        self._req("POST", table, body=rows, prefer="resolution=merge-duplicates,return=minimal", params=params)

    def insert(self, table, rows, returning=False):
        if not rows:
            return [] if returning else None
        return self._req("POST", table, body=rows, prefer="return=representation" if returning else "return=minimal")

    def patch(self, table, params, body):
        self._req("PATCH", table, body=body, prefer="return=minimal", params=params)

    # -- state
    def get_state(self, key, default=None):
        rows = self.select("sync_state", {"select": "value", "key": f"eq.{key}"})
        return rows[0]["value"] if rows else default

    def set_state(self, key, value):
        self.upsert("sync_state", [{"key": key, "value": str(value), "updated_at": iso(now_utc())}])

    # -- seasons
    def seasons(self):
        rows = self.select("seasons", {"select": "year,week1_tuesday"})
        if not rows:
            return dict(config.SEASONS_FALLBACK)
        return {int(r["year"]): date.fromisoformat(r["week1_tuesday"]) for r in rows}

    # -- domain
    def upcoming_games(self):
        return self.select("games", {"select": "*", "commence_time": f"gte.{iso(now_utc())}", "order": "commence_time"})

    def latest_quotes(self, event_ids):
        if not event_ids:
            return []
        ids = ",".join(f'"{e}"' for e in event_ids)
        return self.select("odds_latest", {"select": "*", "event_id": f"in.({ids})"})

    def injury_items(self):
        # ESPN returns ~800 items; PostgREST default page is 1000, ask for more to be safe.
        return self._req("GET", "injury_items", params={"select": "*", "limit": "5000"}) or []

    def recent_alerts(self, since):
        return self.select("alerts", {"select": "fingerprint,event_id,market,side,gap_points,gap_cents,sent_at",
                                      "sent_at": f"gte.{iso(since)}"})

    def start_run(self, kind):
        rows = self.insert("runs", [{"run_kind": kind, "started_at": iso(now_utc())}], returning=True)
        return rows[0]["id"]

    def finish_run(self, run_id, **fields):
        fields["finished_at"] = iso(now_utc())
        self.patch("runs", {"id": f"eq.{run_id}"}, fields)

    def heartbeat(self, note):
        self.insert("heartbeat", [{"note": note}])


class LocalStore:
    """Dry-run store: JSON files under STATE_DIR. Good enough to exercise every code path without accounts."""

    def __init__(self, root=config.STATE_DIR):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.tables = {}
        for t in ["games", "odds_latest", "odds_snapshots", "injury_items", "injury_events", "alerts", "runs", "sync_state", "heartbeat"]:
            p = self.root / f"{t}.json"
            self.tables[t] = json.loads(p.read_text()) if p.exists() else []

    def _save(self, t):
        (self.root / f"{t}.json").write_text(json.dumps(self.tables[t], indent=1, default=str))

    def _pk(self, table):
        return {"games": ("id",), "odds_latest": ("event_id", "bookmaker", "market", "outcome"),
                "injury_items": ("espn_item_id",), "sync_state": ("key",)}.get(table)

    def upsert(self, table, rows, on_conflict=None):
        pk = self._pk(table)
        data = self.tables[table]
        index = {tuple(r[k] for k in pk): i for i, r in enumerate(data)}
        for r in rows:
            k = tuple(r[c] for c in pk)
            if k in index:
                data[index[k]].update(r)
            else:
                index[k] = len(data)
                data.append(dict(r))
        self._save(table)

    def insert(self, table, rows, returning=False):
        data = self.tables[table]
        out = []
        for r in rows:
            r = dict(r)
            r.setdefault("id", len(data) + 1)
            data.append(r)
            out.append(r)
        self._save(table)
        return out if returning else None

    def patch(self, table, params, body):
        key, val = next(iter(params.items()))
        val = val.split("eq.", 1)[1]
        for r in self.tables[table]:
            if str(r.get(key)) == val:
                r.update(body)
        self._save(table)

    def get_state(self, key, default=None):
        for r in self.tables["sync_state"]:
            if r["key"] == key:
                return r["value"]
        return default

    def set_state(self, key, value):
        self.upsert("sync_state", [{"key": key, "value": str(value), "updated_at": iso(now_utc())}])

    def seasons(self):
        return dict(config.SEASONS_FALLBACK)

    def upcoming_games(self):
        n = iso(now_utc())
        return sorted([g for g in self.tables["games"] if g["commence_time"] >= n], key=lambda g: g["commence_time"])

    def latest_quotes(self, event_ids):
        s = set(event_ids)
        return [q for q in self.tables["odds_latest"] if q["event_id"] in s]

    def injury_items(self):
        return list(self.tables["injury_items"])

    def recent_alerts(self, since):
        s = iso(since)
        return [a for a in self.tables["alerts"] if a["sent_at"] >= s]

    def start_run(self, kind):
        return self.insert("runs", [{"run_kind": kind, "started_at": iso(now_utc())}], returning=True)[0]["id"]

    def finish_run(self, run_id, **fields):
        fields["finished_at"] = iso(now_utc())
        self.patch("runs", {"id": f"eq.{run_id}"}, fields)

    def heartbeat(self, note):
        self.insert("heartbeat", [{"note": note, "at": iso(now_utc())}])


def make_store():
    return LocalStore() if config.DRY_RUN else SupabaseStore()
