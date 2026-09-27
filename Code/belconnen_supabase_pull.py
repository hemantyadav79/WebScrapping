"""
RECOVERY: pull every Belconnen record back from Supabase into belconnen_details.json.

Use this when the local belconnen_details.json was lost/truncated but the data is
safe in the Supabase `belconnen_sold` table. It:
  1. downloads ALL rows (paginated — PostgREST caps at 1000/req),
  2. maps the snake_case columns back to the original field names,
  3. writes belconnen_details.json ATOMICALLY (never truncates an existing file),
  4. refreshes belconnen_summary.json, and
  5. reconciles belconnen_state.json so a later --resume skips the recovered rows
     instead of re-scraping them.

Usage:
    pip install supabase
    python belconnen_supabase_pull.py
    python belconnen_supabase_pull.py --no-reconcile-state   # only rewrite details.json
"""
from __future__ import annotations
import argparse, hashlib, json, os, re, sys
from pathlib import Path

CONFIG = Path("config/belconnen.json")
PAGE = 1000  # PostgREST default max rows per request

# numeric fields restored to int (they round-trip through Supabase as text)
NUMERIC = {"Bedrooms", "Bathrooms", "Parking", "EER", "Building Size",
           "Viewed (page views)", "Image Count"}


def to_column(key: str) -> str:
    k = key.lower().replace("'", "")
    return re.sub(r"[^a-z0-9]+", "_", k).strip("_") or "col"


def original_keys():
    """The canonical record keys, recovered from the scraper itself."""
    try:
        sys.path.insert(0, ".")
        from webarchiver.belconnen_research import _detail_record
        return list(_detail_record({}, {}).keys())
    except Exception:
        # fallback list (keep in sync with _detail_record) if the module can't import
        return ["id", "Address", "Street", "Suburb", "State", "Postcode", "Property Type",
                "Bedrooms", "Bathrooms", "Parking", "EER", "Building Size", "Price / Sold Price",
                "Status", "Sold Date", "Listing ID", "Viewed (page views)", "Last Updated",
                "Listed Date", "Description", "Features", "Key Details", "Other / Block Details",
                "What's Nearby", "Property History", "Agent Name", "Agent Phone", "Agency",
                "Agency Phone", "Source Locality", "Source Type", "Image Count", "Image URLs",
                "Local Image Paths", "Listing URL"]


def atomic_write_json(obj, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(obj, indent=2, ensure_ascii=False, default=str)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text); f.flush(); os.fsync(f.fileno())
    os.replace(tmp, path)


def main() -> int:
    ap = argparse.ArgumentParser(description="Recover belconnen_details.json from Supabase.")
    ap.add_argument("--no-reconcile-state", action="store_true",
                    help="only rewrite details.json; don't touch belconnen_state.json")
    args = ap.parse_args()

    if not CONFIG.exists():
        print(f"ERROR: {CONFIG} not found — run from the project root."); return 1
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    url = (cfg.get("supabase_url") or "").strip()
    key = (cfg.get("supabase_key") or "").strip()
    table = cfg.get("supabase_table", "belconnen_sold")
    out = Path(cfg.get("output_dir", "website_backup/AllhomesBelconnen"))
    details_path = out / "belconnen_details.json"

    if not (url and key):
        print("ERROR: supabase_url / supabase_key missing in config/belconnen.json"); return 1
    try:
        from supabase import create_client
    except ImportError:
        print("ERROR: supabase package not installed. Run:  pip install supabase"); return 1

    client = create_client(url, key)
    rev = {to_column(k): k for k in original_keys()}  # snake_case -> original field name

    # 1) download all rows, paginated
    rows = []
    start = 0
    while True:
        resp = client.table(table).select("*").range(start, start + PAGE - 1).execute()
        batch = resp.data or []
        if not batch:
            break
        rows.extend(batch)
        print(f"  fetched {len(rows)} rows ...")
        if len(batch) < PAGE:
            break
        start += PAGE
    if not rows:
        print("No rows found in Supabase — nothing to recover."); return 1

    # 2) map columns back to original field names (+ restore numeric ints)
    records = []
    for r in rows:
        rec = {}
        for col, val in r.items():
            name = rev.get(col)
            if name is None:
                continue
            if name in NUMERIC:
                try:
                    val = int(float(val)) if str(val).strip() not in ("", "N/A", "None") else 0
                except (ValueError, TypeError):
                    val = 0
            rec[name] = val
        records.append(rec)

    # stable order: suburb then address
    records.sort(key=lambda d: (str(d.get("Suburb", "")), str(d.get("Address", ""))))

    # safety: back up whatever is currently on disk before overwriting
    if details_path.exists():
        bak = details_path.with_name("belconnen_details.beforerecover.json")
        try:
            os.replace(details_path, bak)
            print(f"  backed up current file -> {bak.name}")
        except OSError:
            pass

    # 3) write details.json atomically
    atomic_write_json(records, details_path)
    print(f"RECOVERED {len(records)} records -> {details_path}")

    # 4) refresh summary
    atomic_write_json({
        "localities": None, "unique_properties": len(records),
        "details_captured": len(records), "failed": 0, "output_dir": str(out),
        "note": "recovered from Supabase",
    }, out / "belconnen_summary.json")

    # 5) reconcile state so --resume skips recovered rows
    if not args.no_reconcile_state:
        state_path = out / "belconnen_state.json"
        if state_path.exists():
            try:
                st = json.loads(state_path.read_text(encoding="utf-8", errors="ignore"))
            except Exception:
                st = {}
            done = set(st.get("details_done", []))
            urls = {rec.get("Listing URL") for rec in records if rec.get("Listing URL")}
            urls.discard(None); urls.discard("N/A")
            before = len(done)
            done |= urls
            st["details_done"] = sorted(done)
            atomic_write_json(st, state_path)
            print(f"  reconciled state: details_done {before} -> {len(done)} (recovered rows will be skipped on --resume)")
        else:
            print("  (no belconnen_state.json found — resume will rebuild it)")

    print("\nDone. Verify the file opens, then you can safely --resume.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
