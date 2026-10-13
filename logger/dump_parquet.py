"""Weekly backup: pull every table from Supabase and write Parquet files under data/dumps/<date>/.
Also writes a heartbeat row so the free-tier project never looks idle."""
import sys
from datetime import datetime, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from .store import SupabaseStore

TABLES = ["seasons", "games", "odds_latest", "odds_snapshots", "injury_items", "injury_events", "alerts", "runs", "sync_state", "heartbeat", "bets", "model_lines"]


def fetch_all(store: SupabaseStore, table: str):
    return store.select_all(table, {"select": "*"}, order=_order(table))


def _order(table):
    return {"seasons": "year", "games": "id", "odds_latest": "event_id", "sync_state": "key", "injury_items": "espn_item_id", "model_lines": "game_key"}.get(table, "id")


def main():
    store = SupabaseStore()
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out = Path("data/dumps") / day
    out.mkdir(parents=True, exist_ok=True)
    total = 0
    for t in TABLES:
        rows = fetch_all(store, t)
        total += len(rows)
        table = pa.Table.from_pylist(rows) if rows else pa.table({})
        pq.write_table(table, out / f"{t}.parquet")
        print(f"{t}: {len(rows)} rows")
    store.heartbeat(f"weekly dump {day}: {total} rows")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
