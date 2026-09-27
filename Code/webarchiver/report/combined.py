"""Cross-site combined outputs + lightweight market analytics.

After every configured site has been scraped, this module reads each site's
``listings.json``, merges them into a single deduplicated dataset, and writes:

  combined/combined.{json,csv,xlsx}     -> unique properties across all sites
  combined/duplicates.{json,csv,xlsx}   -> rows dropped as duplicates
  combined/analytics.{json,xlsx}        -> the metrics derivable from the data

Duplicates are detected on a normalised street address (case/space/punctuation
insensitive). The first occurrence is kept in the combined file; any later row
with the same address is moved to the duplicates file.
"""
from __future__ import annotations

import json
import logging
import re
import statistics
from pathlib import Path
from typing import Any

from webarchiver.report.exporters import to_csv, to_excel, to_json

_STATE_RE = re.compile(
    r"\b(ACT|NSW|NT|QLD|SA|TAS|VIC|WA)\b\.?\s*\d{0,4}\s*$", re.I
)
_NUM_RE = re.compile(r"[-+]?\d[\d,]*\.?\d*")


def _norm_address(addr: str) -> str:
    a = (addr or "").lower().strip()
    a = re.sub(r"[.,/#!$%^&*;:{}=\-_`~()]", " ", a)
    a = re.sub(r"\s+", " ", a).strip()
    return a


def _suburb_from_address(addr: str) -> str:
    if not addr:
        return ""
    parts = [p.strip() for p in addr.split(",") if p.strip()]
    if not parts:
        return ""
    last = parts[-1]
    # last segment is usually "<Suburb> STATE POSTCODE" or just "STATE POSTCODE"
    candidate = _STATE_RE.sub("", last).strip()
    if candidate:
        return candidate
    if len(parts) >= 2:
        return _STATE_RE.sub("", parts[-2]).strip()
    return ""


def _to_number(val: Any) -> float | None:
    if val is None:
        return None
    m = _NUM_RE.search(str(val))
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", ""))
    except ValueError:
        return None


def _infer_source(rows: list[dict]) -> str:
    """Guess the source site from listing URLs when config doesn't say."""
    for r in rows[:25]:
        u = (r.get("Listing URL") or "").lower()
        if "realestate.com.au" in u:
            return "realestate"
        if "allhomes.com.au" in u:
            return "allhomes"
    return ""


def _ingest(jpath: Path, site_name: str, source: str, logger: logging.Logger) -> list[dict]:
    try:
        data = json.loads(jpath.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("combined: could not read %s: %s", jpath, exc)
        return []
    src = source or _infer_source(data)
    out = []
    for r in data:
        r = dict(r)
        r["Source Site"] = r.get("Source Site") or src or site_name
        r["Source Dataset"] = site_name
        if not r.get("Suburb"):
            r["Suburb"] = _suburb_from_address(r.get("Address", ""))
        out.append(r)
    logger.info("combined: loaded %d rows from '%s'", len(out), site_name)
    return out


def _load_site_rows(config, logger: logging.Logger) -> list[dict]:
    """Merge the current config's sites AND any other site folders already on
    disk under output_root, so separate per-config runs still combine."""
    root = Path(config.global_config.output_root)
    rows: list[dict] = []
    seen_dirs: set[str] = set()

    # 1) sites in the current config (authoritative source tags)
    for site in config.websites:
        jpath = root / site.name / "listings.json"
        if jpath.exists():
            rows.extend(_ingest(jpath, site.name, site.listing_source, logger))
            seen_dirs.add(site.name)
        else:
            logger.info("combined: no listings.json for '%s' (skipped)", site.name)

    # 2) any other previously-scraped site folders under output_root
    if root.exists():
        for child in sorted(root.iterdir()):
            if not child.is_dir() or child.name in seen_dirs or child.name == "combined":
                continue
            jpath = child / "listings.json"
            if jpath.exists():
                rows.extend(_ingest(jpath, child.name, "", logger))
                seen_dirs.add(child.name)
    return rows


def _dedupe(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    seen: dict[str, dict] = {}
    unique: list[dict] = []
    dups: list[dict] = []
    for r in rows:
        key = _norm_address(r.get("Address", ""))
        if not key:
            unique.append(r)  # cannot match without an address; keep it
            continue
        if key in seen:
            d = dict(r)
            d["Duplicate Of"] = seen[key].get("Source Dataset", "")
            dups.append(d)
        else:
            seen[key] = r
            unique.append(r)
    return unique, dups


# --------------------------------------------------------------------------
# analytics
# --------------------------------------------------------------------------
def _agent_rankings(rows: list[dict]) -> list[dict]:
    agg: dict[str, dict] = {}
    for r in rows:
        name = (r.get("Agency") or r.get("Agent") or "").strip()
        if not name:
            continue
        a = agg.setdefault(name, {"Agency / Agent": name, "Listings": 0, "_prices": [],
                                  "Suburbs": set()})
        a["Listings"] += 1
        p = _to_number(r.get("Price"))
        if p:
            a["_prices"].append(p)
        if r.get("Suburb"):
            a["Suburbs"].add(r["Suburb"])
    out = []
    for a in agg.values():
        prices = a.pop("_prices")
        a["Suburbs Covered"] = len(a.pop("Suburbs"))
        a["Median Price"] = round(statistics.median(prices)) if prices else ""
        out.append(a)
    out.sort(key=lambda x: x["Listings"], reverse=True)
    for i, a in enumerate(out, 1):
        a["Rank"] = i
    return out


def _market_share_by_suburb(rows: list[dict]) -> list[dict]:
    sub: dict[str, dict] = {}
    for r in rows:
        s = (r.get("Suburb") or "").strip()
        if not s:
            continue
        d = sub.setdefault(s, {"Suburb": s, "Total Listings": 0, "_ag": {}})
        d["Total Listings"] += 1
        ag = (r.get("Agency") or r.get("Agent") or "Unknown").strip() or "Unknown"
        d["_ag"][ag] = d["_ag"].get(ag, 0) + 1
    out = []
    for d in sub.values():
        ag = d.pop("_ag")
        top, cnt = max(ag.items(), key=lambda kv: kv[1])
        d["Top Agency"] = top
        d["Top Agency Listings"] = cnt
        d["Top Agency Share %"] = round(100 * cnt / d["Total Listings"], 1)
        d["Active Agencies"] = len(ag)
        out.append(d)
    out.sort(key=lambda x: x["Total Listings"], reverse=True)
    return out


def _days_on_market(rows: list[dict]) -> list[dict]:
    from datetime import datetime

    def parse_dt(v):
        if not v:
            return None
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%d %B %Y", "%d/%m/%Y"):
            try:
                return datetime.strptime(str(v)[:19], fmt)
            except ValueError:
                continue
        return None

    sub: dict[str, list] = {}
    for r in rows:
        dom = _to_number(r.get("Days On Market"))
        if dom is None:
            ld, cd = parse_dt(r.get("Listed Date")), parse_dt(r.get("Contract Date"))
            if ld and cd:
                dom = (cd - ld).days
        if dom is None or dom < 0 or dom > 3650:
            continue
        sub.setdefault((r.get("Suburb") or "All").strip() or "All", []).append(dom)
    out = [
        {"Suburb": s, "Avg Days On Market": round(statistics.mean(v), 1),
         "Median": round(statistics.median(v)), "Sample Size": len(v)}
        for s, v in sub.items()
    ]
    out.sort(key=lambda x: x["Sample Size"], reverse=True)
    return out


def _most_viewed(rows: list[dict], top: int = 30) -> list[dict]:
    scored = []
    for r in rows:
        v = _to_number(r.get("Page Views"))
        if v is None:
            continue
        scored.append({
            "Address": r.get("Address", ""), "Suburb": r.get("Suburb", ""),
            "Page Views": int(v), "Price": r.get("Price", ""),
            "Agency": r.get("Agency", ""), "Source Site": r.get("Source Site", ""),
            "Listing URL": r.get("Listing URL", ""),
        })
    scored.sort(key=lambda x: x["Page Views"], reverse=True)
    return scored[:top]


def _suburb_demand(rows: list[dict]) -> list[dict]:
    sub: dict[str, dict] = {}
    for r in rows:
        s = (r.get("Suburb") or "").strip()
        if not s:
            continue
        d = sub.setdefault(s, {"Suburb": s, "Listings": 0, "_views": []})
        d["Listings"] += 1
        v = _to_number(r.get("Page Views"))
        if v is not None:
            d["_views"].append(v)
    out = []
    total_listings = sum(d["Listings"] for d in sub.values()) or 1
    for d in sub.values():
        views = d.pop("_views")
        d["Total Page Views"] = int(sum(views)) if views else ""
        d["Avg Page Views"] = round(statistics.mean(views), 1) if views else ""
        d["Listing Share %"] = round(100 * d["Listings"] / total_listings, 1)
        out.append(d)
    # demand score: blend of normalised listing volume + normalised avg views
    max_listings = max((d["Listings"] for d in out), default=1) or 1
    max_views = max(
        (d["Avg Page Views"] for d in out if isinstance(d["Avg Page Views"], (int, float))),
        default=0,
    ) or 1
    for d in out:
        av = d["Avg Page Views"] if isinstance(d["Avg Page Views"], (int, float)) else 0
        score = 50 * (d["Listings"] / max_listings) + 50 * (av / max_views)
        d["Demand Score (0-100)"] = round(min(100, score), 1)
    out.sort(key=lambda x: x["Demand Score (0-100)"], reverse=True)
    return out


def build_analytics(rows: list[dict]) -> dict[str, list[dict]]:
    return {
        "Agent Sales Rankings": _agent_rankings(rows),
        "Market Share by Suburb": _market_share_by_suburb(rows),
        "Avg Days on Market": _days_on_market(rows),
        "Most Viewed Properties": _most_viewed(rows),
        "Suburb Demand": _suburb_demand(rows),
    }


def build_combined_outputs(config, logger: logging.Logger | None = None) -> dict:
    log = logger or logging.getLogger("webarchiver.combined")
    rows = _load_site_rows(config, log)
    out_dir = Path(config.global_config.output_root) / "combined"
    if not rows:
        log.info("combined: no listings found across sites; nothing to merge")
        return {"combined": 0, "duplicates": 0}

    unique, dups = _dedupe(rows)
    log.info("combined: %d total rows -> %d unique, %d duplicates",
             len(rows), len(unique), len(dups))

    to_json(unique, out_dir / "combined.json")
    to_csv(unique, out_dir / "combined.csv")
    to_excel({"combined": unique}, out_dir / "combined.xlsx")

    to_json(dups, out_dir / "duplicates.json")
    to_csv(dups, out_dir / "duplicates.csv")
    to_excel({"duplicates": dups}, out_dir / "duplicates.xlsx")

    analytics = build_analytics(unique)
    to_json(analytics, out_dir / "analytics.json")
    to_excel(analytics, out_dir / "analytics.xlsx")
    log.info("combined: wrote combined/, duplicates/ and analytics into %s", out_dir)

    return {
        "combined": len(unique),
        "duplicates": len(dups),
        "analytics_sheets": list(analytics.keys()),
        "out_dir": str(out_dir),
    }
