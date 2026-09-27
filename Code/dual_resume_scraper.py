"""
Bidirectional, resumable scraping coordinator.

Goal: run the SAME job in TWO terminals at once —
  * terminal 1 works the frontier FORWARD  (first listing -> last)
  * terminal 2 works the frontier REVERSE  (last listing  -> first)
…they converge in the middle, never scrape the same item twice, and BOTH
sides are independently crash-safe / resumable.

Why a SQLite store instead of writing listings.json from both processes:
  Two processes atomically replacing one JSON file race and corrupt it
  (last-writer-wins wipes the other's rows). SQLite (WAL mode) gives us safe
  concurrent writes + an atomic "claim" so each item is processed exactly once.
  listings.json is then exported from the DB by a SINGLE writer — always clean.

CLI
---
  # 1) seed the work list once (ids derived from the URL, so it's idempotent)
  python dual_resume_scraper.py init  --seed urls.txt

  # 2) terminal 1  (resumes from where the FRONT left off)
  python dual_resume_scraper.py run   --direction forward  --live-export

  # 3) terminal 2  (resumes from where the BACK left off)
  python dual_resume_scraper.py run   --direction reverse

  # anytime: rebuild listings.json from everything done so far
  python dual_resume_scraper.py export --out listings.json

  # anytime: see progress + where the two heads have met
  python dual_resume_scraper.py status

Plug your real scraper into fetch_one().  Everything else is generic.
"""
from __future__ import annotations
import argparse, hashlib, json, os, sqlite3, sys, time, socket

DB_PATH = os.environ.get("DUAL_DB", "progress.db")


# ----------------------------------------------------------------------------
# >>> PLUG YOUR SCRAPER HERE <<<
# Replace the body with a call into your webarchiver extractor for one listing.
# It must return a dict (the listing record) or raise on failure.
def fetch_one(key: str) -> dict:
    time.sleep(0.02)  # simulate network/scrape latency (demo only)
    return {"url": key, "scraped_at": round(time.time(), 3), "ok": True}
# ----------------------------------------------------------------------------


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30, isolation_level=None)  # autocommit; we manage txns
    conn.execute("PRAGMA journal_mode=WAL;")      # multiple writers/readers
    conn.execute("PRAGMA synchronous=NORMAL;")    # durable enough + fast
    conn.execute("PRAGMA busy_timeout=15000;")    # wait instead of 'database is locked'
    conn.execute(
        """CREATE TABLE IF NOT EXISTS items(
               id      TEXT PRIMARY KEY,   -- md5(key); stable + idempotent
               pos     INTEGER UNIQUE,     -- order in the frontier (front..back)
               key     TEXT NOT NULL,      -- the URL / listing key
               status  TEXT NOT NULL,      -- pending | in_progress | done | error
               owner   TEXT,               -- which worker holds it
               data    TEXT,               -- JSON record once done
               updated REAL)""")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_status_pos ON items(status,pos);")
    return conn


def _id(key: str) -> str:
    return hashlib.md5(key.strip().encode("utf-8")).hexdigest()


def cmd_init(args):
    """Load the frontier once. INSERT OR IGNORE => safe to re-run; never resets done rows."""
    conn = connect()
    lines = [l.strip() for l in open(args.seed, encoding="utf-8") if l.strip()]
    # keep the largest existing pos so re-seeding appends new keys at the end
    base = conn.execute("SELECT COALESCE(MAX(pos),-1) FROM items").fetchone()[0] + 1
    added = 0
    conn.execute("BEGIN")
    for i, key in enumerate(lines):
        try:
            conn.execute("INSERT OR IGNORE INTO items(id,pos,key,status,updated) VALUES(?,?,?, 'pending', ?)",
                         (_id(key), base + added, key, time.time()))
            if conn.total_changes:  # crude per-row add count
                pass
            added += 1
        except sqlite3.IntegrityError:
            pass
    conn.execute("COMMIT")
    n = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    print(f"init: seed had {len(lines)} keys; frontier now has {n} items total.")


def _requeue_stale(conn, stale_secs: int):
    """Resume safety: any item left 'in_progress' by a crashed worker is returned to the queue."""
    cutoff = time.time() - stale_secs
    cur = conn.execute("UPDATE items SET status='pending', owner=NULL "
                       "WHERE status='in_progress' AND (updated IS NULL OR updated < ?)", (cutoff,))
    if cur.rowcount:
        print(f"resume: requeued {cur.rowcount} stale in-progress item(s).")


def _claim(conn, direction: str, owner: str):
    """Atomically take the next pending item from the chosen end of the frontier."""
    order = "ASC" if direction == "forward" else "DESC"
    for _ in range(50):  # retry on lost races
        conn.execute("BEGIN IMMEDIATE")  # take the write lock so the claim is atomic
        row = conn.execute(
            f"SELECT id,key,pos FROM items WHERE status='pending' ORDER BY pos {order} LIMIT 1").fetchone()
        if row is None:
            conn.execute("COMMIT")
            return None
        changed = conn.execute(
            "UPDATE items SET status='in_progress', owner=?, updated=? WHERE id=? AND status='pending'",
            (owner, time.time(), row[0])).rowcount
        conn.execute("COMMIT")
        if changed:
            return row  # (id, key, pos)
    return None


def cmd_run(args):
    conn = connect()
    owner = f"{args.direction}@{socket.gethostname()}:{os.getpid()}"
    _requeue_stale(conn, args.stale)
    done_here = 0
    print(f"run: {owner} starting ({args.direction}); resuming from the "
          f"{'front' if args.direction=='forward' else 'back'} of the frontier.")
    while True:
        claim = _claim(conn, args.direction, owner)
        if claim is None:
            print(f"run: no more pending items — {args.direction} worker is done "
                  f"(processed {done_here} this session). The two heads have met.")
            break
        item_id, key, pos = claim
        try:
            rec = fetch_one(key)
            conn.execute("UPDATE items SET status='done', data=?, owner=?, updated=? WHERE id=?",
                         (json.dumps(rec, ensure_ascii=False), owner, time.time(), item_id))
        except Exception as exc:  # noqa: BLE001 — keep going; mark for retry
            conn.execute("UPDATE items SET status='pending', owner=NULL, updated=? WHERE id=?",
                         (time.time(), item_id))
            print(f"  ! pos {pos} failed ({exc}); requeued.")
            time.sleep(0.5)
            continue
        done_here += 1
        if args.live_export and done_here % args.live_export_every == 0:
            _export(conn, args.out)  # ONLY the worker started with --live-export writes the file
        if args.limit and done_here >= args.limit:
            print(f"run: hit --limit {args.limit}; stopping (safe to resume).")
            break
    if args.live_export:
        _export(conn, args.out)


def _export(conn, out_path: str):
    """Single-writer, atomic export of every completed record -> listings.json (ordered front..back)."""
    rows = conn.execute("SELECT data FROM items WHERE status='done' AND data IS NOT NULL ORDER BY pos").fetchall()
    records = [json.loads(r[0]) for r in rows]
    tmp = out_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)
    os.replace(tmp, out_path)  # atomic
    return len(records)


def cmd_export(args):
    conn = connect()
    n = _export(conn, args.out)
    print(f"export: wrote {n} records -> {args.out}")


def cmd_status(args):
    conn = connect()
    counts = dict(conn.execute("SELECT status, COUNT(*) FROM items GROUP BY status").fetchall())
    total = sum(counts.values())
    fwd = conn.execute("SELECT MAX(pos) FROM items WHERE status='done'").fetchone()[0]
    rev = conn.execute("SELECT MIN(pos) FROM items WHERE status='done'").fetchone()[0]
    print("status:")
    for k in ("pending", "in_progress", "done", "error"):
        print(f"  {k:12} {counts.get(k,0)}")
    print(f"  {'total':12} {total}")
    if fwd is not None:
        print(f"  forward head reached pos {fwd}; reverse head reached pos {rev} "
              f"(they meet when pending = 0).")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Bidirectional, resumable scraping coordinator.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init", help="seed the frontier from a file of URLs/keys")
    p.add_argument("--seed", required=True)
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("run", help="process items from one end of the frontier")
    p.add_argument("--direction", choices=["forward", "reverse"], default="forward")
    p.add_argument("--stale", type=int, default=300, help="requeue in-progress items older than N seconds (crash recovery)")
    p.add_argument("--limit", type=int, default=0, help="stop after N items this session (0 = until done)")
    p.add_argument("--live-export", action="store_true", help="this worker also writes listings.json periodically")
    p.add_argument("--live-export-every", type=int, default=50)
    p.add_argument("--out", default="listings.json")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("export", help="rebuild listings.json from the store")
    p.add_argument("--out", default="listings.json")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("status", help="show progress and where the two heads have met")
    p.set_defaults(func=cmd_status)

    args = ap.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
