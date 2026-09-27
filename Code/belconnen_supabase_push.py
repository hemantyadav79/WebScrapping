"""One-shot: push the already-scraped Belconnen records to Supabase.

Usage:
    pip install supabase
    python belconnen_supabase_push.py

Reads supabase_url / supabase_key / supabase_table and the details file from
config/belconnen.json, then upserts every record (idempotent, keyed on `id`).
Run it any time to (re)sync the local JSON into Supabase.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

CONFIG = Path("config/belconnen.json")


def to_column(key: str) -> str:
    k = key.lower().replace("'", "")
    return re.sub(r"[^a-z0-9]+", "_", k).strip("_") or "col"


def main() -> int:
    if not CONFIG.exists():
        print(f"ERROR: {CONFIG} not found (run from the project root).")
        return 1
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    url = (cfg.get("supabase_url") or "").strip()
    key = (cfg.get("supabase_key") or "").strip()
    table = cfg.get("supabase_table", "belconnen_sold")
    out_dir = Path(cfg.get("output_dir", "."))
    details_path = out_dir / "belconnen_details.json"

    if not (url and key):
        print("ERROR: supabase_url / supabase_key missing in config/belconnen.json")
        return 1
    if not details_path.exists():
        print(f"ERROR: details file not found: {details_path}")
        return 1

    try:
        from supabase import create_client
    except ImportError:
        print("ERROR: the supabase package is not installed. Run:  pip install supabase")
        return 1

    records = json.loads(details_path.read_text(encoding="utf-8"))
    print(f"Loaded {len(records)} records from {details_path}")

    rows = []
    for rec in records:
        row = {to_column(k): v for k, v in rec.items()}
        if not row.get("id"):
            seed = (rec.get("Listing URL") or rec.get("Address") or "")
            row["id"] = hashlib.md5(seed.encode("utf-8")).hexdigest()
        rows.append(row)

    try:
        client = create_client(url, key)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR creating client: {exc}")
        return 1

    pushed = 0
    try:
        for i in range(0, len(rows), 500):
            chunk = rows[i:i + 500]
            client.table(table).upsert(chunk, on_conflict="id").execute()
            pushed += len(chunk)
            print(f"  upserted {pushed}/{len(rows)} ...")
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR during upsert (pushed {pushed} before failing): {exc}")
        print("If this mentions a missing column/table, re-run the create-table SQL "
              "in the SAME Supabase project, then run this script again.")
        return 1

    print(f"\nDONE: {pushed} rows upserted into '{table}'. Refresh the Supabase table to see them.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
