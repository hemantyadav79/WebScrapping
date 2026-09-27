"""Optional Supabase upload of scraped data for the dashboard.

Behaviour:
  * Runs only if BOTH a Supabase URL and a key are present in the environment.
    Recognised env vars:
      URL : SUPABASE_URL
      KEY : SUPABASE_SERVICE_ROLE_KEY | SUPABASE_SERVICE_KEY | SUPABASE_KEY
            | SUPABASE_PRIVATE_KEY | SUPABASE_ANON_KEY | SUPABASE_PUBLIC_KEY
    If they're missing, it logs one line and does nothing (no error).
  * Requires the `supabase` package (pip install supabase). If absent, it skips
    with a hint.
  * One table per source file, named after the file/site (sanitised), so the
    data is easy to find: e.g. RealestateCanberra -> "realestatecanberra",
    combined.json -> "combined".
  * Tables must exist first (the client can't create them). We auto-write a
    `combined/supabase_setup.sql` with CREATE TABLE statements you run once in
    the Supabase SQL editor; then re-run and the rows upsert.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from pathlib import Path

_URL_ENV = "SUPABASE_URL"
_KEY_ENVS = (
    "SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_SERVICE_KEY", "SUPABASE_KEY",
    "SUPABASE_PRIVATE_KEY", "SUPABASE_ANON_KEY", "SUPABASE_PUBLIC_KEY",
)


def _table_name(stem: str) -> str:
    t = re.sub(r"[^a-z0-9_]+", "_", stem.lower()).strip("_")
    return t or "data"


def _row_id(row: dict) -> str:
    basis = str(row.get("Listing URL") or row.get("Address") or json.dumps(row, sort_keys=True))
    return hashlib.md5(basis.encode("utf-8")).hexdigest()


def _collect_sources(config) -> list[tuple[str, Path]]:
    """(table_name, json_path) for each site listings.json + the combined file."""
    root = Path(config.global_config.output_root)
    out: list[tuple[str, Path]] = []
    for site in config.websites:
        p = root / site.name / "listings.json"
        if p.exists():
            out.append((_table_name(site.name), p))
    cj = root / "combined" / "combined.json"
    if cj.exists():
        out.append(("combined", cj))
    return out


def _write_setup_sql(sources, root: Path, logger) -> None:
    lines = ["-- Run once in the Supabase SQL editor to create the tables.\n"]
    for table, path in sources:
        try:
            rows = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        cols = set()
        for r in rows[:200]:
            cols.update(r.keys())
        cols = sorted(cols)
        coldefs = ",\n  ".join(f'"{c}" text' for c in cols)
        lines.append(
            f'create table if not exists public."{table}" (\n'
            f'  id text primary key,\n  {coldefs}\n);\n'
        )
    dest = root / "combined" / "supabase_setup.sql"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text("\n".join(lines), encoding="utf-8")
    logger.info("Wrote Supabase schema SQL -> %s", dest)


def upload_to_supabase(config, logger: logging.Logger | None = None) -> dict:
    log = logger or logging.getLogger("webarchiver.supabase")
    sources = _collect_sources(config)
    root = Path(config.global_config.output_root)
    if sources:
        _write_setup_sql(sources, root, log)  # always emit schema (handy even before upload)

    # Prefer values in the config file; fall back to environment variables.
    gc = config.global_config
    url = (getattr(gc, "supabase_url", "") or os.environ.get(_URL_ENV, "")).strip()
    key = (getattr(gc, "supabase_key", "")
           or next((os.environ[k] for k in _KEY_ENVS if os.environ.get(k)), "")).strip()
    if not (url and key):
        log.info("Supabase env not set (SUPABASE_URL + a key) -> skipping upload.")
        return {"uploaded": 0, "skipped": "no-env"}

    try:
        from supabase import create_client
    except ImportError:
        log.warning("Supabase env present but `supabase` not installed "
                    "(pip install supabase) -> skipping upload.")
        return {"uploaded": 0, "skipped": "no-lib"}

    client = create_client(url, key)
    total = 0
    for table, path in sources:
        try:
            rows = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            log.warning("supabase: cannot read %s: %s", path, exc); continue
        if not rows:
            continue
        for r in rows:
            r["id"] = _row_id(r)
        try:
            for i in range(0, len(rows), 500):  # chunked upsert
                client.table(table).upsert(rows[i:i + 500], on_conflict="id").execute()
            total += len(rows)
            log.info("supabase: upserted %d rows -> table '%s'", len(rows), table)
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "supabase: upload to '%s' failed (%s). If the table is missing, "
                "run combined/supabase_setup.sql in the SQL editor, then re-run.",
                table, exc,
            )
    return {"uploaded": total, "tables": [t for t, _ in sources]}
