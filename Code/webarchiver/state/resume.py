"""Resume snapshots: persist collected listings + details so a crashed run can
continue without re-scraping or losing what was already extracted.

These files live under ``<site>/_resume/`` and are rewritten at each checkpoint.
They are separate from ``crawl_state.json`` (which holds frontier/pages/assets).
"""
from __future__ import annotations

import json
from dataclasses import asdict, fields
from pathlib import Path

from webarchiver.listings.models import ListingRecord

_ALLOWED = {f.name for f in fields(ListingRecord)}


def save_listings(listings: list[ListingRecord], path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [asdict(r) for r in listings]
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(rows, ensure_ascii=False, default=str), encoding="utf-8")
    tmp.replace(path)  # atomic so a crash mid-write can't corrupt it


def load_listings(path: str | Path) -> list[ListingRecord]:
    p = Path(path)
    if not p.is_file():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    out: list[ListingRecord] = []
    for d in data:
        clean = {k: v for k, v in d.items() if k in _ALLOWED}
        try:
            out.append(ListingRecord(**clean))
        except TypeError:
            continue
    return out


def save_details(details: list[dict], path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(details, ensure_ascii=False, default=str), encoding="utf-8")
    tmp.replace(path)


def load_details(path: str | Path) -> list[dict]:
    p = Path(path)
    if not p.is_file():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []
