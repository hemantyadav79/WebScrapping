"""Standalone Belconnen (allhomes) sold-history research scraper — DATA ONLY.

This is a self-contained scraper that does NOT modify any existing file. It
reuses the project's existing helpers (DrissionPage engine, the GraphQL helper,
and the allhomes detail parser) but adds its own orchestration for the Belconnen
research task:

  * Paginate the sold history of the Belconnen DIVISION via the
    ``updateHistoryForLocality`` GraphQL persisted query (same hash as the rest
    of the project), collecting each property's detail-page URL.
  * Optionally do the same for every STREET in Belconnen and every nearby
    (surrounding) SUBURB — discovered from the research page and the
    ``/research/<slug>/streets`` page.
  * Visit each unique property page and extract the full detail (description,
    features, sold status/date, key details, other/block details, what's nearby,
    property history, listing id, views, last-updated, agent + agency).
    NO images are downloaded — this is data only.
  * Rotate the browser fingerprint every N minutes (stop, relaunch a fresh
    Chromium with a new user-agent/session, continue) to dodge Akamai.
  * Fully resumable via a checkpoint file.

Run:
    python -m webarchiver.belconnen_research --config config/belconnen.json
    python -m webarchiver.belconnen_research --config config/belconnen.json --resume
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import random
import re
import shutil
import tempfile
import time
from pathlib import Path

from webarchiver.config.models import GlobalConfig
from webarchiver.fetch.drission_engine import DrissionRenderEngine
from webarchiver.listings.allhomes_detail import parse_allhomes_detail
from webarchiver.listings.allhomes_graphql import (
    DEFAULT_HASH, OPERATION, graphql_endpoint, parse_response,
)
from webarchiver.report.exporters import to_csv, to_excel, to_json

log = logging.getLogger("webarchiver.belconnen")

# A small pool of realistic desktop Chrome user-agents for fingerprint rotation.
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
]

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def strip_html(s: str) -> str:
    if not s:
        return ""
    s = s.replace("<br>", "\n").replace("<br/>", "\n").replace("<br />", "\n")
    return _WS.sub(" ", _TAG.sub(" ", s)).strip()


def street_from_address(line1: str) -> str:
    """'86/64 College Street' -> 'College Street'; '12 Doyle Terrace' -> 'Doyle Terrace'."""
    line1 = (line1 or "").split(",")[0].strip()
    toks = line1.split()
    if toks and re.search(r"\d", toks[0]):  # drop a leading unit/number token
        toks = toks[1:]
    return " ".join(toks).strip()


def build_history_body(slug: str, ltype: str, page: int,
                       page_size: int = 10, sha256_hash: str = DEFAULT_HASH) -> str:
    """GraphQL POST body for one sold-history page of a locality.

    Same operation/hash as the rest of the project; ``ltype`` is DIVISION for
    suburbs and STREET for streets.
    """
    return json.dumps({
        "operationName": OPERATION,
        "variables": {
            "locality": {"slug": slug, "type": ltype},
            "filters": {"beds": {"lower": 0}, "baths": {"lower": 0}, "parks": {"lower": 0}},
            "duration": {"unit": "ALL"},
            "sort": {"type": "SOLD_AGE", "order": "DESC"},
            "page": page,
            "pageSize": page_size,
        },
        "extensions": {"persistedQuery": {"version": 1, "sha256Hash": sha256_hash}},
    })


def _next_data(html: str) -> dict | None:
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html or "", re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except (json.JSONDecodeError, ValueError):
        return None


def parse_locality_overview(html: str) -> dict:
    """From a /research/<slug> page, return the locality, its streets and its
    surrounding suburbs (each {name, slug, type}), and history.totalPages."""
    d = _next_data(html) or {}
    loc = (((d.get("props") or {}).get("pageProps") or {}).get("page") or {}).get("locality") or {}
    streets = [{"name": s.get("name"), "slug": s.get("slug"), "type": "STREET"}
               for s in (loc.get("streets") or []) if s.get("slug")]
    suburbs = [{"name": s.get("name"), "slug": s.get("slug"), "type": "DIVISION"}
               for s in (loc.get("surroundingSuburbs") or []) if s.get("slug")]
    total = ((loc.get("history") or {}).get("totalPages"))
    return {
        "name": loc.get("name"), "slug": loc.get("slug"),
        "type": loc.get("type") or "DIVISION",
        "total_pages": total, "streets": streets, "suburbs": suburbs,
    }


def parse_streets_page(html: str) -> list[dict]:
    """From /research/<slug>/streets, return every street {name, slug, type}."""
    d = _next_data(html)
    out: list[dict] = []
    if d:
        def find_streets(o, depth=0):
            if depth > 8:
                return None
            if isinstance(o, dict):
                for k, v in o.items():
                    if k == "streets" and isinstance(v, list) and v and isinstance(v[0], dict) and v[0].get("slug"):
                        return v
                    r = find_streets(v, depth + 1)
                    if r is not None:
                        return r
            elif isinstance(o, list) and o:
                return find_streets(o[0], depth + 1)
            return None
        arr = find_streets(d) or []
        out = [{"name": s.get("name"), "slug": s.get("slug"), "type": "STREET"} for s in arr if s.get("slug")]
    if not out:  # fallback: scrape the street-pattern links from the DOM
        for slug in sorted(set(re.findall(r"/research/([a-z0-9-]+-act-\d{4})", html or ""))):
            out.append({"name": slug, "slug": slug, "type": "STREET"})
    return out


def _node_basic(node: dict, locality: dict) -> dict | None:
    """Pull the detail URL + sale summary out of one GraphQL history node."""
    listing = node.get("listing") or {}
    url = listing.get("url") or ""
    if not url:
        return None
    addr = node.get("address") or {}
    transfer = node.get("transfer") or {}
    div = (addr.get("division") or {})
    return {
        "listing_url": url,
        "address": addr.get("line1") or addr.get("formattedFull") or "",
        "suburb": div.get("name") or "",
        "postcode": addr.get("postcode") or "",
        "sold_date": (node.get("date") or transfer.get("contractDate") or "")[:10],
        "sold_price": transfer.get("price") or listing.get("priceLabel") or "",
        "source_locality_slug": locality.get("slug"),
        "source_locality_name": locality.get("name") or locality.get("slug"),
        "source_type": locality.get("type"),
    }


def _detail_record(basic: dict, det: dict | None) -> dict:
    """Combine node summary + parsed detail into one DATA-ONLY record (no images)."""
    det = det or {}
    def join_kv(items):
        return " | ".join(f"{k.get('label','')}: {k.get('value','')}".strip(": ")
                          for k in (items or []) if isinstance(k, dict))
    def join_nearby(items):
        return " | ".join(
            (f"{p.get('name') or p.get('title','')}"
             + (f" ({p.get('category') or p.get('type','')})" if (p.get('category') or p.get('type')) else "")).strip()
            for p in (items or []) if isinstance(p, dict))
    def join_history(items):
        out = []
        for ev in (items or []):
            t = ev.get("transfer") or {}
            dt = str(ev.get("date") or t.get("contractDate") or "")[:10]
            pr = t.get("price") or t.get("label") or ""
            out.append(f"{dt}: {pr}".strip(": "))
        return " | ".join(o for o in out if o)
    block = det.get("block_information") or {}
    block_str = " | ".join(f"{k}: {v}" for k, v in block.items()
                           if v and k != "unimproved_value_history")
    agents = det.get("agents") or []
    images = det.get("image_urls") or []
    rec = {
        "id": hashlib.md5(((basic.get("listing_url") or basic.get("address") or "")).encode("utf-8")).hexdigest(),
        "Address": det.get("address") or basic.get("address"),
        "Street": street_from_address(det.get("address") or basic.get("address")),
        "Suburb": det.get("suburb") or basic.get("suburb"),
        "State": det.get("state", ""),
        "Postcode": det.get("postcode") or basic.get("postcode"),
        "Property Type": det.get("property_type", ""),
        "Bedrooms": det.get("bedrooms", ""),
        "Bathrooms": det.get("bathrooms", ""),
        "Parking": det.get("parking_total", ""),
        "EER": det.get("eer", ""),
        "Building Size": det.get("building_size", ""),
        "Price / Sold Price": det.get("price") or basic.get("sold_price"),
        "Status": det.get("status", ""),
        "Sold Date": basic.get("sold_date", ""),
        "Listing ID": det.get("listing_id", ""),
        "Viewed (page views)": det.get("page_views", ""),
        "Last Updated": (det.get("last_updated") or "")[:19],
        "Listed Date": (det.get("listed_date") or "")[:19],
        "Description": strip_html(det.get("description", "")),
        "Features": "; ".join(det.get("features_list") or []),
        "Key Details": join_kv(det.get("key_details")),
        "Other / Block Details": block_str,
        "What's Nearby": join_nearby(det.get("whats_nearby")),
        "Property History": join_history(det.get("sale_history")),
        "Agent Name": "; ".join(a.get("name", "") for a in agents),
        "Agent Phone": "; ".join(a.get("phone", "") for a in agents if a.get("phone")),
        "Agency": (det.get("agency") or {}).get("name", ""),
        "Agency Phone": (det.get("agency") or {}).get("phone", ""),
        "Source Locality": basic.get("source_locality_name"),
        "Source Type": basic.get("source_type"),
        "Image Count": len(images),
        "Image URLs": "; ".join(images),
        "Local Image Paths": "; ".join(basic.get("local_image_paths") or []),
        "Listing URL": basic.get("listing_url"),
    }
    # Fill empties: numeric fields -> 0, everything else -> "N/A".
    numeric = {"Bedrooms", "Bathrooms", "Parking", "EER", "Building Size",
               "Viewed (page views)", "Image Count"}
    for k, v in rec.items():
        if v is None or v == "" or v == []:
            rec[k] = 0 if k in numeric else "N/A"
    return rec


class BelconnenResearchScraper:
    def __init__(self, cfg: dict) -> None:
        self.cfg = cfg
        self.out = Path(cfg.get("output_dir", "website_backup/AllhomesBelconnen"))
        self.out.mkdir(parents=True, exist_ok=True)
        self.state_path = self.out / "belconnen_state.json"
        self.details_path = self.out / "belconnen_details.json"
        self.hash = cfg.get("graphql_hash", DEFAULT_HASH)
        self.page_size = int(cfg.get("page_size", 10))
        self.rotate_secs = float(cfg.get("fingerprint_rotate_minutes", 30)) * 60
        self.detail_delay = float(cfg.get("detail_delay_sec", 1.0))
        self.max_pages = int(cfg.get("max_pages_per_locality", 0))  # 0 = all
        self.chunk = int(cfg.get("graphql_chunk", 20))
        self._engine = None
        self._ua_idx = 0
        self._cycle_start = 0.0
        self._profile_dir = None    # managed temp Chrome profile (deleted on rotation)
        # resumable state
        self.localities: list[dict] = []
        self.pages_done: dict[str, int] = {}
        self.urls: dict[str, dict] = {}
        self.details: list[dict] = []
        self.details_done: set[str] = set()
        self.attempts: dict[str, int] = {}   # url -> failed-attempt count (blocked/denied)
        self.failed_urls: set[str] = set()    # parked after too many attempts (dead-letter)
        self.max_attempts = int(cfg.get("max_detail_attempts", 5))
        self._failed = 0
        self._sb = None            # supabase client (lazy)
        self._sb_pushed = 0        # index into self.details already uploaded
        self._sb_warned = False
        self.sb_table = cfg.get("supabase_table", "belconnen_sold")

    # ---------- supabase ----------
    @staticmethod
    def _to_column(key: str) -> str:
        k = key.lower().replace("'", "")
        k = re.sub(r"[^a-z0-9]+", "_", k).strip("_")
        return k or "col"

    def _supabase_columns(self) -> list[str]:
        # canonical column order derived from an empty record
        rec = _detail_record({}, {})
        return [self._to_column(k) for k in rec.keys()]

    def _write_supabase_sql(self) -> None:
        cols = self._supabase_columns()
        defs = ",\n  ".join(f'"{c}" text' for c in cols if c != "id")
        sql = ("-- Run once in the Supabase SQL editor to create the Belconnen table.\n"
               f'create table if not exists public."{self.sb_table}" (\n'
               f'  "id" text primary key,\n  {defs}\n);\n')
        (self.out / "belconnen_supabase_setup.sql").write_text(sql, encoding="utf-8")
        log.info("Supabase schema SQL -> %s", self.out / "belconnen_supabase_setup.sql")

    def _init_supabase(self) -> None:
        url = (self.cfg.get("supabase_url", "") or os.environ.get("SUPABASE_URL", "")).strip()
        key = (self.cfg.get("supabase_key", "")
               or os.environ.get("SUPABASE_SECRET_KEY", "")
               or os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
               or os.environ.get("SUPABASE_KEY", "")).strip()
        if not (url and key):
            log.info("Supabase not configured (no url/key) -> upload disabled.")
            return
        try:
            from supabase import create_client
        except ImportError:
            log.warning("Supabase url/key set but `supabase` not installed (pip install supabase) -> upload disabled.")
            return
        try:
            self._sb = create_client(url, key)
            log.info("Supabase upload enabled -> table '%s'", self.sb_table)
        except Exception as exc:  # noqa: BLE001
            log.warning("Supabase init failed: %s", exc)

    def _push_supabase(self) -> None:
        if self._sb is None or self._sb_pushed >= len(self.details):
            return
        batch = self.details[self._sb_pushed:]
        rows = []
        for rec in batch:
            row = {self._to_column(k): v for k, v in rec.items()}
            if not row.get("id"):  # backfill id for records scraped before id existed
                row["id"] = hashlib.md5(((rec.get("Listing URL") or rec.get("Address") or "")).encode("utf-8")).hexdigest()
            rows.append(row)
        try:
            for i in range(0, len(rows), 500):
                self._sb.table(self.sb_table).upsert(rows[i:i + 500], on_conflict="id").execute()
            self._sb_pushed = len(self.details)
            log.info("Supabase: upserted %d rows (total %d)", len(rows), self._sb_pushed)
        except Exception as exc:  # noqa: BLE001
            if not self._sb_warned:
                log.warning("Supabase upsert failed (%s). If the table/columns are missing, run "
                            "belconnen_supabase_setup.sql in the SQL editor.", exc)
                self._sb_warned = True

    # ---------- logging (console + logs/crawl.log + logs/error.log) ----------
    def _setup_logging(self) -> None:
        import logging.handlers
        from webarchiver.core.logging_setup import configure_root
        level = str(self.cfg.get("log_level", "INFO")).upper()
        configure_root(level)  # shared console handler on the 'webarchiver' logger
        logs_dir = self.out / "logs"; logs_dir.mkdir(parents=True, exist_ok=True)
        fmt = logging.Formatter("%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
                                "%Y-%m-%d %H:%M:%S")
        if not any(isinstance(h, logging.handlers.RotatingFileHandler) for h in log.handlers):
            ch = logging.handlers.RotatingFileHandler(
                logs_dir / "crawl.log", maxBytes=10*1024*1024, backupCount=5, encoding="utf-8")
            ch.setFormatter(fmt)
            eh = logging.handlers.RotatingFileHandler(
                logs_dir / "error.log", maxBytes=10*1024*1024, backupCount=5, encoding="utf-8")
            eh.setLevel(logging.WARNING); eh.setFormatter(fmt)
            log.addHandler(ch); log.addHandler(eh)
        log.setLevel(getattr(logging, level, logging.INFO))

    # ---------- engine + fingerprint rotation ----------
    def _build_config(self) -> GlobalConfig:
        # Keep the browser's REAL Chrome user-agent by default. A hard-coded UA
        # that doesn't match the actual browser is itself a fingerprint tell that
        # Akamai flags on the very first request, so only override when asked.
        rotate_ua = bool(self.cfg.get("rotate_user_agent", False))
        over = bool(self.cfg.get("override_user_agent", False)) or rotate_ua
        ua = self.cfg.get("user_agent") or (
            USER_AGENTS[self._ua_idx % len(USER_AGENTS)] if rotate_ua else "")
        kw = dict(
            request_timeout=int(self.cfg.get("request_timeout", 45)),
            override_user_agent=over,
            headless=bool(self.cfg.get("headless", False)),
            page_load_wait=float(self.cfg.get("page_load_wait", 4.0)),
            block_detection=bool(self.cfg.get("block_detection", True)),
            stealth=True,
            proxy=str(self.cfg.get("proxy", "") or ""),
            use_ollama=False,
            # Reuse the user's configured profile if set; otherwise a managed temp
            # profile that is DELETED on every rotation (see _stop_engine) so 89
            # fingerprint cycles can no longer leave 89 Chrome caches on disk.
            chrome_user_data_dir=(str(self.cfg.get("chrome_user_data_dir", "") or "")
                                  or (self._profile_dir or "")),
            chrome_debugger_address=str(self.cfg.get("chrome_debugger_address", "") or ""),
        )
        if ua:
            kw["user_agent"] = ua
        return GlobalConfig(**kw)

    async def _start_engine(self) -> None:
        # Managed temp profile (only when the user hasn't pinned one and we're not
        # attaching to their own Chrome). Created fresh each cycle, removed on stop.
        if not str(self.cfg.get("chrome_user_data_dir", "") or "").strip() \
                and not str(self.cfg.get("chrome_debugger_address", "") or "").strip():
            self._profile_dir = tempfile.mkdtemp(prefix="belc_chrome_")
        cfg = self._build_config()
        self._engine = DrissionRenderEngine(cfg, headless=cfg.headless, proxy_manager=None)
        await self._engine.start()
        self._cycle_start = time.monotonic()
        log.info("Engine started (fingerprint #%d, UA=...%s)", self._ua_idx + 1, cfg.user_agent[-18:])

    async def _stop_engine(self) -> None:
        if self._engine is not None:
            try:
                await self._engine.close()
            except Exception:  # noqa: BLE001
                pass
            self._engine = None
        # Delete the managed temp profile so caches don't accumulate across rotations.
        if self._profile_dir:
            shutil.rmtree(self._profile_dir, ignore_errors=True)
            self._profile_dir = None

    @staticmethod
    def _clean_stale_profiles() -> None:
        """Remove leftover managed Chrome profiles from previously-crashed runs."""
        tmp = Path(tempfile.gettempdir())
        try:
            for p in tmp.glob("belc_chrome_*"):
                shutil.rmtree(p, ignore_errors=True)
        except OSError:
            pass

    async def _maybe_rotate(self, force: bool = False) -> None:
        if self._engine is None:
            self._ua_idx += 1
            await self._start_engine()
            return
        if self.cfg.get("chrome_debugger_address"):
            return  # attached to your own Chrome — never close/rotate it
        if force or (time.monotonic() - self._cycle_start) >= self.rotate_secs:
            log.info("Rotating browser fingerprint (interval reached) — restarting engine")
            await self._stop_engine()
            await asyncio.sleep(random.uniform(3, 8))  # brief cool-down
            self._ua_idx += 1
            await self._start_engine()

    # ---------- state ----------
    @staticmethod
    def _read_json_tolerant(path: Path):
        """Load JSON, tolerating trailing junk from a half-finished/raced write."""
        txt = path.read_text(encoding="utf-8", errors="ignore")
        try:
            return json.loads(txt)
        except json.JSONDecodeError:
            try:
                obj, _end = json.JSONDecoder().raw_decode(txt)  # first complete object
                log.warning("State file had trailing data — recovered the valid part.")
                return obj
            except (json.JSONDecodeError, ValueError):
                return None

    def _load_state(self) -> None:
        if self.state_path.exists():
            st = self._read_json_tolerant(self.state_path)
            if st:
                self.localities = st.get("localities", [])
                self.pages_done = st.get("pages_done", {})
                self.urls = st.get("urls", {})
                self.details_done = set(st.get("details_done", []))
                self.attempts = {k: int(v) for k, v in (st.get("attempts") or {}).items()}
                self.failed_urls = set(st.get("failed_urls", []))
                log.info("Resumed: %d localities, %d urls, %d details done",
                         len(self.localities), len(self.urls), len(self.details_done))
            else:
                log.warning("Could not read state; starting fresh")
        if self.details_path.exists():
            d = self._read_json_tolerant(self.details_path)
            self.details = d if isinstance(d, list) else []

    def _save_state(self) -> None:
        payload = json.dumps({
            "localities": self.localities, "pages_done": self.pages_done,
            "urls": self.urls, "details_done": sorted(self.details_done),
            "attempts": self.attempts, "failed_urls": sorted(self.failed_urls),
        }, ensure_ascii=False)
        tmp = self.state_path.with_suffix(".json.tmp")
        try:
            tmp.write_text(payload, encoding="utf-8")
            tmp.replace(self.state_path)  # atomic; the existing state is never truncated
        except Exception as exc:  # noqa: BLE001 - e.g. disk full / file briefly locked
            try:
                if tmp.exists():
                    tmp.unlink()  # drop the partial temp; keep the good existing state file
            except OSError:
                pass
            log.warning("could not write %s (%s) — keeping previous state file", self.state_path, exc)

    def _save_details(self) -> None:
        # Atomic (see exporters.to_json) — the existing file is never wiped on a
        # failed save. A save error (e.g. disk full) is logged, not raised, so the
        # run keeps its in-memory data and retries at the next checkpoint.
        try:
            to_json(self.details, self.details_path)
        except OSError as exc:
            log.error("Could not save %s (%s). In-memory data is safe; "
                      "free disk space and it will be written at the next checkpoint.",
                      self.details_path, exc)

    # ---------- discovery ----------
    async def _discover(self) -> None:
        if self.localities:
            return  # already discovered (resume)
        div_url = self.cfg["division_url"].rstrip("/")
        log.info("Discovering localities from %s", div_url)
        res = await self._engine.fetch(div_url)
        if not getattr(res, "ok", False):
            raise RuntimeError(f"could not load division page: {getattr(res,'error','')}")
        ov = parse_locality_overview(res.html)
        localities = [{"name": ov["name"], "slug": ov["slug"], "type": ov["type"],
                       "total_pages": ov.get("total_pages")}]
        if self.cfg.get("include_streets", True):
            await self._maybe_rotate()
            sres = await self._engine.fetch(div_url + "/streets")
            streets = parse_streets_page(sres.html) if getattr(sres, "ok", False) else ov["streets"]
            localities += [{**s, "total_pages": None} for s in streets]
            log.info("  + %d streets", len(streets))
        if self.cfg.get("include_suburbs", True):
            subs = [s for s in ov["suburbs"] if s["slug"] != ov["slug"]]
            localities += [{**s, "total_pages": None} for s in subs]
            log.info("  + %d surrounding suburbs", len(subs))
        # de-dup by slug
        seen, uniq = set(), []
        for l in localities:
            if l["slug"] and l["slug"] not in seen:
                seen.add(l["slug"]); uniq.append(l)
        self.localities = uniq
        self._save_state()
        log.info("Total localities to process: %d", len(self.localities))

    # ---------- URL collection ----------
    async def _collect_urls(self) -> None:
        for idx, loc in enumerate(self.localities, 1):
            await self._collect_locality(idx, loc)

    async def _collect_locality(self, idx: int, loc: dict) -> None:
        """Collect every property URL for one locality.

        Each locality has a DIFFERENT page count (some 10, some 39, some 0).
        We discover the real total from the site's own response: we probe one
        page first to learn ``totalPages``, then pull the rest in chunks bounded
        by that total. This means:
          * an empty street (0 pages)  -> 1 request, then we move on;
          * a 10-page street           -> stops exactly at page 10;
          * a 39-page street           -> stops exactly at page 39;
        with no wasted requests past the real last page and no infinite loops.
        """
        slug, ltype = loc["slug"], loc.get("type", "DIVISION")
        n = len(self.localities)
        # Honour the include_streets / include_suburbs switches even when the
        # localities were already discovered in a previous (resumed) run.
        m = re.search(r"/research/([^/?#]+)", self.cfg.get("division_url", ""))
        div_slug = m.group(1) if m else ""
        role = "street" if ltype == "STREET" else ("division" if slug == div_slug else "suburb")
        if role == "street" and not self.cfg.get("include_streets", True):
            log.info("[%d/%d] %s — skipped (include_streets=false; covered by the division)", idx, n, slug)
            return
        if role == "suburb" and not self.cfg.get("include_suburbs", True):
            log.info("[%d/%d] %s — skipped (include_suburbs=false)", idx, n, slug)
            return
        loc_url = f"https://www.allhomes.com.au/research/{slug}"
        endpoint = graphql_endpoint(loc_url)
        total = loc.get("total_pages")               # may be known (division) or None
        page = self.pages_done.get(slug, 0) + 1
        cap = int(self.cfg.get("safety_page_cap", 2000))
        if total is not None and page > total:
            log.info("[%d/%d] %s — already collected (%s pages)", idx, n, slug, total)
            return
        log.info("[%d/%d] Collecting %s (%s) from page %d%s", idx, n, slug, ltype, page,
                 f" of {total}" if total is not None else " — page count unknown, will detect")
        while True:
            if self.max_pages and page > self.max_pages:
                break
            # Probe 1 page while the total is unknown; once known, use full chunks.
            if total is None:
                span = 1
            else:
                span = min(self.chunk, total - page + 1)
            if self.max_pages:
                span = min(span, self.max_pages - page + 1)
            if span <= 0:
                break
            await self._maybe_rotate()
            bodies = [build_history_body(slug, ltype, p, self.page_size, self.hash)
                      for p in range(page, page + span)]
            texts = await self._engine.browser_post_many(loc_url, endpoint, bodies, wait_each=0.5)
            round_nodes = 0
            retry = []  # pages with a transient failure (e.g. Akamai returned non-JSON)
            for i, text in enumerate(texts):
                p = page + i
                nodes, tot, err = parse_response(text)
                if tot is not None and total is None:
                    total = tot
                    loc["total_pages"] = tot
                    log.info("    %s -> %d page(s) total", slug, tot)
                if err:
                    if err == "PersistedQueryNotFound":
                        # Skip just THIS locality (its sales are covered by the division anyway).
                        log.warning("    skipping %s (persisted-query rejected for this locality type)", slug)
                        self.pages_done[slug] = total if total is not None else p
                        self._save_state()
                        return
                    log.warning("    %s page %d: %s (queued for retry)", slug, p, err)
                    retry.append(p)
                    continue
                for nd in (nodes or []):
                    b = _node_basic(nd, loc)
                    if b and b["listing_url"] not in self.urls:
                        self.urls[b["listing_url"]] = b
                round_nodes += len(nodes or [])
                self.pages_done[slug] = p
            # Retry transient-failure pages up to twice so real data isn't dropped.
            for _att in range(2):
                if not retry:
                    break
                await asyncio.sleep(random.uniform(2, 5))
                rbodies = [build_history_body(slug, ltype, p, self.page_size, self.hash) for p in retry]
                rtexts = await self._engine.browser_post_many(loc_url, endpoint, rbodies, wait_each=0.6)
                still = []
                for p, text in zip(retry, rtexts):
                    nodes, _t, err = parse_response(text)
                    if err:
                        still.append(p)
                        continue
                    for nd in (nodes or []):
                        b = _node_basic(nd, loc)
                        if b and b["listing_url"] not in self.urls:
                            self.urls[b["listing_url"]] = b
                    round_nodes += len(nodes or [])
                if len(still) < len(retry):
                    log.info("    %s recovered %d/%d retried page(s)", slug, len(retry) - len(still), len(retry))
                retry = still
            if retry:
                log.warning("    %s: %d page(s) still failed after retries: %s", slug, len(retry), retry)
            self._save_state()
            page += span
            # progress for large localities (e.g. the 479-page division)
            if total and total > self.chunk:
                log.info("    %s: %d/%d pages collected (urls so far: %d)",
                         slug, min(page - 1, total), total, len(self.urls))
            # ---- stop conditions (handle 0 / short / long uniformly) ----
            if total == 0:
                log.info("    %s has no sold listings — skipping", slug)
                break
            if total is not None and page > total:
                break                                  # reached the real last page (ANY size)
            if total is None and round_nodes == 0:
                break                                  # unknown total + empty page = end
            # Safety cap applies ONLY when the total is unknown — it must never
            # truncate a locality whose real totalPages is large (200, 479, ...).
            if total is None and page > cap:
                log.warning("    %s reached safety cap (%d pages, total unknown)", slug, cap)
                break
        log.info("    %s done (%s pages) — running unique-URL total: %d",
                 slug, total if total is not None else "?", len(self.urls))

    # ---------- detail crawl (no images) ----------
    async def _download_images(self, urls: list[str]) -> list[str]:
        """Download property images into <out>/images/ and return local paths.
        Only used when config download_images is true (default false)."""
        if not urls:
            return []
        from webarchiver.core.naming import safe_component
        img_dir = self.out / "images"; img_dir.mkdir(parents=True, exist_ok=True)
        paths = []
        for u in urls:
            try:
                res = await self._engine.fetch_binary(u)
                if getattr(res, "ok", False) and res.body:
                    name = safe_component(u.rsplit("/", 1)[-1].split("?")[0]) or f"img_{len(paths)}.jpg"
                    fp = img_dir / name
                    fp.write_bytes(res.body)
                    paths.append(f"images/{name}")
            except Exception:  # noqa: BLE001
                continue
        return paths

    def _record_failure(self, url: str, reason: str) -> None:
        """A blocked/denied/empty detail is NOT marked done, so it is retried on a
        later run — but after max_detail_attempts it is parked (dead-letter) so the
        crawl can still reach completion. Re-queue them later with --retry-failed."""
        self._failed += 1
        n = self.attempts.get(url, 0) + 1
        self.attempts[url] = n
        if n >= self.max_attempts:
            self.failed_urls.add(url)
            log.warning("detail GIVING UP after %d attempts (%s) — parked in failed list: %s",
                        n, reason, url)
        else:
            log.warning("detail attempt %d/%d failed (%s) — will retry on a later run: %s",
                        n, self.max_attempts, reason, url)

    async def _crawl_details(self) -> None:
        # skip already-done AND parked (dead-letter) URLs; everything else is retried
        pending = [u for u in self.urls if u not in self.details_done and u not in self.failed_urls]
        parked = len(self.failed_urls)
        log.info("Detail crawl: %d pending of %d total URLs (%d parked after max retries)",
                 len(pending), len(self.urls), parked)
        for i, url in enumerate(pending, 1):
            await self._maybe_rotate()
            try:
                res = await self._engine.fetch(url)
            except Exception as exc:  # noqa: BLE001
                self._record_failure(url, f"fetch error: {exc}"); continue
            if not getattr(res, "ok", False):
                self._record_failure(url, f"access denied / fetch failed: {getattr(res, 'error', '')}"); continue
            det = parse_allhomes_detail(res.html, url)
            if not det:
                self._record_failure(url, "parse empty (likely blocked)"); continue
            basic = self.urls[url]
            if self.cfg.get("download_images", False):  # OFF by default — data only
                basic["local_image_paths"] = await self._download_images(det.get("image_urls") or [])
            self.details.append(_detail_record(basic, det))
            self.details_done.add(url)
            self.attempts.pop(url, None)  # success — clear its retry counter
            if i % 20 == 0 or i == len(pending):
                self._save_details(); self._save_state(); self._write_summary()
                await asyncio.to_thread(self._push_supabase)
                log.info("  detail %d/%d (saved)", i, len(pending))
            if self.detail_delay > 0:
                await asyncio.sleep(self.detail_delay + random.uniform(0, self.detail_delay))
        self._save_details(); self._save_state(); self._write_summary()
        await asyncio.to_thread(self._push_supabase)

    def _write_tables(self) -> None:
        if not self.details:
            return
        cols = list(self.details[0].keys())
        if self.cfg.get("export_csv", True):
            to_csv(self.details, self.out / "belconnen_details.csv", columns=cols)
        if self.cfg.get("export_excel", True):
            to_excel({"belconnen": self.details}, self.out / "belconnen_details.xlsx", columns={"belconnen": cols})
        log.info("Wrote %d records to %s", len(self.details), self.out)

    def _write_website(self) -> None:
        """Publish a standalone HTML explorer (data only) into the output folder."""
        tpl = Path(__file__).with_name("belconnen_view.html")
        try:
            (self.out / "belconnen.html").write_text(tpl.read_text(encoding="utf-8"), encoding="utf-8")
            (self.out / "serve.bat").write_text(
                "@echo off\r\ncd /d %~dp0\r\nstart \"\" http://localhost:8000/belconnen.html\r\n"
                "python -m http.server 8000\r\n", encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            log.warning("could not write website: %s", exc)

    async def run(self, resume: bool = False, retry_failed: bool = False) -> None:
        self._setup_logging()
        log.info("=" * 70)
        log.info("Belconnen research scraper starting (resume=%s) -> %s", resume, self.out)
        log.info("Division: %s | streets=%s suburbs=%s | rotate=%smin | images=NO",
                 self.cfg.get("division_url"), self.cfg.get("include_streets"),
                 self.cfg.get("include_suburbs"), self.cfg.get("fingerprint_rotate_minutes"))
        log.info("=" * 70)
        self._clean_stale_profiles()  # remove leftover Chrome profiles from past crashes
        self._init_supabase()
        self._write_supabase_sql()
        if resume:
            self._load_state()
        if retry_failed and self.failed_urls:
            log.info("Re-queueing %d previously-parked (failed) URLs for another attempt", len(self.failed_urls))
            self.failed_urls.clear()
            self.attempts.clear()
        await self._start_engine()
        self._write_website()
        try:
            await self._discover()
            await self._collect_urls()
            await self._crawl_details()
            self._write_tables()
            self._write_website()
            self._write_summary()
            log.info("=" * 70)
            log.info("DONE — localities=%d, unique properties=%d, details captured=%d, failed=%d",
                     len(self.localities), len(self.urls), len(self.details), self._failed)
            log.info("Output: %s (belconnen_details.json/.csv/.xlsx + belconnen.html)", self.out)
            log.info("=" * 70)
        except Exception:  # noqa: BLE001
            log.exception("Belconnen run failed")
            raise
        finally:
            await self._stop_engine()

    def _write_summary(self) -> None:
        pending_retry = len([u for u in self.urls
                             if u not in self.details_done and u not in self.failed_urls])
        summary = {
            "localities": len(self.localities),
            "unique_properties": len(self.urls),
            "details_captured": len(self.details),
            "pending_retry": pending_retry,         # still to attempt (incl. blocked ones)
            "parked_failed": len(self.failed_urls),  # gave up after max_detail_attempts
            "failed_events": self._failed,
            "output_dir": str(self.out),
        }
        to_json(summary, self.out / "belconnen_summary.json")
        # write the parked URLs out for review / manual re-queue (--retry-failed)
        if self.failed_urls:
            to_json(sorted(self.failed_urls), self.out / "belconnen_failed_urls.json")


def _default_config() -> dict:
    return {
        "output_dir": "website_backup/AllhomesBelconnen",
        "division_url": "https://www.allhomes.com.au/research/belconnen-act-2617",
        "graphql_hash": DEFAULT_HASH,
        "page_size": 10,
        "graphql_chunk": 20,
        "include_streets": True,
        "include_suburbs": True,
        "max_pages_per_locality": 0,
        "fingerprint_rotate_minutes": 30,
        "detail_delay_sec": 1.0,
        "headless": False,
        "page_load_wait": 4.0,
        "request_timeout": 45,
        "block_detection": True,
        "export_csv": True,
        "export_excel": True,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Belconnen allhomes sold-history research scraper (data only)")
    ap.add_argument("-c", "--config", default="")
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--retry-failed", action="store_true",
                    help="also re-attempt URLs that were parked after max_detail_attempts")
    args = ap.parse_args(argv)
    from webarchiver.core.logging_setup import configure_root
    configure_root("INFO")
    cfg = _default_config()
    if args.config and Path(args.config).is_file():
        cfg.update(json.loads(Path(args.config).read_text(encoding="utf-8")))
    asyncio.run(BelconnenResearchScraper(cfg).run(resume=args.resume, retry_failed=args.retry_failed))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
