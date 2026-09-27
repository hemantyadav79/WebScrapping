"""WebsiteProcessor: end-to-end pipeline for a single website.

Orchestrates crawl -> persist HTML -> download assets -> screenshots ->
offline link rewrite -> reports, with periodic checkpointing for resume.
Each website is processed independently so one failure cannot abort the run.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlsplit

from webarchiver.archive.link_rewriter import LinkRewriter
from webarchiver.archive.storage import Workspace
from webarchiver.config.models import AppConfig, RenderEngineType, WebsiteConfig
from webarchiver.core.hashing import sha256_text
from webarchiver.core.logging_setup import SiteLoggers
from webarchiver.crawl.crawler import Crawler
from webarchiver.crawl.pagination import plan_pages, plan_param_pages
from webarchiver.crawl.robots import RobotsPolicy
from webarchiver.extract.models import AssetRecord, AssetType, BrokenLink, PageRecord
from webarchiver.extract.page_parser import PageParser
from webarchiver.fetch.engine import RenderEngine
from webarchiver.fetch.factory import build_engine
from webarchiver.listings.extractor import _record_from_map, get_extractor
from webarchiver.listings.models import ListingRecord
from webarchiver.listings.reporter import write_listings
from webarchiver.listings.validator import validate_all
from webarchiver.report.models import SiteSummary
from webarchiver.report.reporters import ReportBuilder
from webarchiver.screenshot.screenshot_engine import ScreenshotService
from webarchiver.state.crawl_state import CrawlState

_CHECKPOINT_EVERY = 25

_FLAG_BY_TYPE = {
    AssetType.IMAGE: "download_images",
    AssetType.VIDEO: "download_videos",
    AssetType.DOCUMENT: "download_documents",
    AssetType.CSS: "download_css",
    AssetType.JS: "download_js",
    AssetType.FONT: "download_fonts",
}


class WebsiteProcessor:
    """Processes one website from crawl to reports."""

    def __init__(
        self,
        app_config: AppConfig,
        site: WebsiteConfig,
        *,
        resume: bool = False,
        engine_factory: Callable[[RenderEngineType, "object"], RenderEngine] | None = None,
    ) -> None:
        self._app = app_config
        self._site = site
        self._resume = resume
        self._engine_factory = engine_factory or build_engine
        self._workspace = Workspace(app_config.global_config.output_root, site.name)
        self._state_path = self._workspace.root / "crawl_state.json"
        self._asset_index: dict[str, AssetRecord] = {}
        self._listings: list[ListingRecord] = []
        self._seen_listings: set = set()
        self._details: list = []
        self._listing_extractor = None
        self._ollama_fallback = None
        self._listings_rejected = 0
        self._avg_quality = 0.0
        self._since_checkpoint = 0
        self._engine = None
        self._state = None
        self._downloader = None
        self._asset_lock = asyncio.Lock()
        self._resume_dir = self._workspace.root / "_resume"
        self._stop_drain = None
        self._drainer = None

    async def run(self) -> SiteSummary:
        gc = self._app.global_config
        self._workspace.create_tree(listings_only=self._site.mode == "listings")

        with SiteLoggers(self._site.name, self._workspace.root / "logs", gc.log_level) as logs:
            return await self._run_inner(logs)

    async def _run_inner(self, logs: SiteLoggers) -> SiteSummary:
        gc = self._app.global_config
        start = time.monotonic()

        state = self._load_or_new_state()
        self._asset_index = {a.url: a for a in state.assets}
        self._state = state
        # On --resume ALWAYS reload what was collected so far. This both (a) lets
        # an unfinished site continue (details retry the URLs that failed/were
        # missed) and (b) guarantees we never overwrite listings.json with an
        # empty in-memory list. A fresh run (no --resume) intentionally starts
        # over and overwrites.
        if self._resume:
            self._reload_resume(state, logs)
            # Re-open the detail phase so still-missing properties get retried.
            if state.status == "completed" and self._site.fetch_details:
                state.status = "crawled"

        engine_type = self._app.resolved_engine(self._site)
        engine = self._engine_factory(engine_type, gc)
        self._engine = engine
        host = self._workspace_host()
        parser = PageParser(
            root_host=host,
            include_subdomains=self._site.include_subdomains,
        )
        if self._site.extract_listings:
            self._listing_extractor = get_extractor(host, self._site.listing_source)
            # Hybrid: rule-based primary, Ollama only as a fallback when rules find nothing.
            if gc.use_ollama and self._site.listing_source != "ollama":
                from webarchiver.llm.ollama_extractor import OllamaListingExtractor
                self._ollama_fallback = OllamaListingExtractor(
                    host=gc.ollama_host, model=gc.ollama_model,
                    timeout=gc.ollama_timeout, logger=logs.crawl,
                )
        robots = RobotsPolicy(gc.user_agent, enabled=gc.respect_robots_txt)

        summary = SiteSummary(name=self._site.name, url=self._site.url)
        summary.output_dir = str(self._workspace.root)

        link_filter = None
        if gc.use_ollama:
            from webarchiver.llm.ollama_filter import OllamaLinkFilter

            link_filter = OllamaLinkFilter(
                host=gc.ollama_host, model=gc.ollama_model,
                timeout=gc.ollama_timeout, logger=logs.crawl,
            )

        try:
            await engine.start()
            await robots.load(self._site.url, engine)
            if link_filter is not None:
                await link_filter.start()  # disables itself if Ollama is unreachable

            if self._site.follow_pagination and self._site.pagination_param:
                explicit_pages = plan_param_pages(
                    self._site.url, self._site.max_pages, self._site.pagination_param
                ) or None
            elif self._site.follow_pagination:
                explicit_pages = plan_pages(self._site.url, self._site.max_pages) or None
            else:
                explicit_pages = None
            if explicit_pages:
                logs.crawl.info(
                    "Following %d ordered pagination pages (no sub-nav detours)",
                    len(explicit_pages),
                )
            crawler = Crawler(
                site=self._site, engine=engine, parser=parser,
                frontier=state.frontier, robots=robots,
                concurrency=gc.concurrency, logger=logs.crawl, error_logger=logs.error,
                link_filter=link_filter, explicit_pages=explicit_pages,
            )

            # Download images in the BACKGROUND, in parallel with crawling, so a
            # crash never loses them and the crawl is never blocked by downloads.
            self._stop_drain = asyncio.Event()
            if self._site.download_images:
                self._drainer = asyncio.create_task(self._asset_drain_loop(state, logs))

            # Publish the website up front so you can open serve.bat and watch the
            # list fill in live as listings.json grows at each checkpoint.
            if self._app.global_config.build_website:
                try:
                    from webarchiver.website.publisher import publish_website
                    publish_website(self._workspace.root)
                except Exception:  # noqa: BLE001
                    pass

            if state.status != "completed":
                if self._site.pagination_graphql:
                    await self._crawl_graphql(engine, state, logs)
                elif self._site.pagination_search_api:
                    await self._crawl_search_api(engine, state, logs)
                elif self._site.pagination_hash:
                    await self._crawl_hash_pages(engine, state, logs)
                else:
                    await crawler.crawl(lambda rec, html: self._on_page(rec, html, state, logs))
                state.status = "crawled"
                self._checkpoint(state, force=True)

            if self._site.fetch_details and self._listings:
                await self._crawl_details(engine, state, logs)

            await self._stop_asset_drain()
            assets = await self._download_assets(engine, logs)
            if self._listing_extractor is not None:
                self._finalise_listings(assets, logs)
            if self._details:
                self._finalise_details(assets, logs)

            broken = [
                BrokenLink(p.url, p.url, p.status_code, p.error or "")
                for p in state.pages if p.error
            ]

            if self._site.mode == "listings":
                # Lightweight: listings + photos only, no full-site archive.
                self._populate_summary(summary, state.pages, assets, broken, {})
                summary.crawl_duration_sec = time.monotonic() - start
                self._write_crawl_report_only(summary, logs)
            else:
                screenshots = await self._capture_screenshots(engine, state.pages, logs)
                if self._site.rewrite_links:
                    self._rewrite_for_offline(state.pages, assets, logs)
                self._populate_summary(summary, state.pages, assets, broken, screenshots)
                summary.crawl_duration_sec = time.monotonic() - start
                ReportBuilder(
                    site=self._site, root_dir=self._workspace.root, logger=logs.crawl
                ).build_all(pages=state.pages, assets=assets, broken=broken, summary=summary)

            self._write_proxy_health(engine, logs)
            if self._app.global_config.build_website:
                try:
                    from webarchiver.website.publisher import publish_website
                    n = publish_website(self._workspace.root)
                    logs.crawl.info("Website published (%d files) -> open serve.bat", n)
                except Exception as exc:  # noqa: BLE001
                    logs.error.warning("Website publish skipped: %s", exc)
            state.assets = assets
            state.status = "completed"
            self._checkpoint(state, force=True)
            logs.crawl.info("Site '%s' complete in %.1fs", self._site.name, summary.crawl_duration_sec)
        except Exception as exc:  # noqa: BLE001 - isolate per-site failures
            logs.error.exception("Site '%s' failed: %s", self._site.name, exc)
            summary.errors.append(str(exc))
        finally:
            await self._stop_asset_drain()
            await engine.close()

        return summary

    # ---- crawl callback ----------------------------------------------
    async def _on_page(self, rec: PageRecord, html: str, state: CrawlState, logs: SiteLoggers) -> None:
        if not rec.error and html:
            # Archive the full page + its assets only in archive/both modes.
            if self._site.mode in ("archive", "both"):
                path = self._workspace.html_path(rec.url)
                self._workspace.write_text(path, html)
                rec.local_path = self._workspace.relpath(path)
                rec.content_hash = sha256_text(html)
                for ref in rec.assets:
                    flag = _FLAG_BY_TYPE.get(ref.asset_type)
                    if flag is None or not getattr(self._site, flag):
                        continue
                    existing = self._asset_index.get(ref.url)
                    if existing is None:
                        existing = AssetRecord(url=ref.url, asset_type=ref.asset_type)
                        self._asset_index[ref.url] = existing
                    existing.referenced_by.add(ref.referenced_by)
            if self._listing_extractor is not None:
                found = self._listing_extractor.extract(html, rec.final_url or rec.url)
                if not found and self._ollama_fallback is not None:
                    found = await asyncio.to_thread(
                        self._ollama_fallback.extract, html, rec.final_url or rec.url
                    )
                if found:
                    self._collect_listings(found, rec.url, logs)
        state.pages.append(rec)
        self._since_checkpoint += 1
        if self._since_checkpoint >= _CHECKPOINT_EVERY:
            self._persist(state, logs)

    def _collect_listings(self, found: list, source: str, logs: SiteLoggers) -> list:
        """De-duplicate, accumulate, and queue media for a batch of listings."""
        from webarchiver.report.combined import _suburb_from_address
        fresh = []
        for r in found:
            # Guarantee suburb + source on every record regardless of which
            # extractor path produced it (Argonaut / search / DOM fallback).
            if not getattr(r, "source_site", ""):
                r.source_site = self._site.listing_source or ""
            if not getattr(r, "suburb", "") and r.address:
                r.suburb = _suburb_from_address(r.address)
            key = (r.address.strip().lower(), r.contract_date)
            if key in self._seen_listings:
                continue
            self._seen_listings.add(key)
            fresh.append(r)
        if fresh:
            self._listings.extend(fresh)
            logs.crawl.info("  %d new listings (%s)", len(fresh), source)
            if self._site.download_images:
                self._register_listing_images(fresh)
            if self._site.download_videos:
                self._register_listing_videos(fresh)
        return fresh

    async def _asset_drain_loop(self, state: CrawlState, logs: SiteLoggers) -> None:
        """Background task: keep downloading newly-queued images while the crawl
        runs, so photos land on disk progressively and never block crawling."""
        try:
            while not self._stop_drain.is_set():
                await self._flush_assets(state, logs)
                try:
                    await asyncio.wait_for(self._stop_drain.wait(), timeout=3.0)
                except asyncio.TimeoutError:
                    pass
            await self._flush_assets(state, logs)  # final pass after crawl ends
        except asyncio.CancelledError:
            pass
        except Exception as exc:  # noqa: BLE001 - never let the drainer kill the run
            logs.error.warning("asset drain loop error: %s", exc)

    async def _stop_asset_drain(self) -> None:
        """Signal the background downloader to finish and wait for it."""
        if self._drainer is not None and not self._drainer.done():
            self._stop_drain.set()
            try:
                await self._drainer
            except Exception:  # noqa: BLE001
                pass
        self._drainer = None

    def _reload_resume(self, state: CrawlState, logs: SiteLoggers) -> None:
        """On --resume, reload already-collected listings + details from disk."""
        from webarchiver.state import resume
        prior = resume.load_listings(self._resume_dir / "listings_state.json")
        if prior:
            self._listings = prior
            for r in prior:
                self._seen_listings.add((r.address.strip().lower(), r.contract_date))
            logs.crawl.info("Resume: reloaded %d listings from snapshot", len(prior))
        details = resume.load_details(self._resume_dir / "details.json")
        if details:
            self._details = details
            logs.crawl.info("Resume: reloaded %d property details", len(details))

    async def _flush_assets(self, state: CrawlState, logs: SiteLoggers) -> None:
        """Download any images discovered so far, NOW, so a crash keeps them."""
        if not self._site.download_images:
            return
        async with self._asset_lock:
            pending = [a for a in self._asset_index.values()
                       if not a.downloaded and not a.error]
            if not pending:
                return
            if self._downloader is None:
                from webarchiver.download.asset_downloader import AssetDownloader
                self._downloader = AssetDownloader(
                    engine=self._engine, workspace=self._workspace,
                    concurrency=self._app.global_config.concurrency, logger=logs.download,
                )
            await self._downloader.download_all(pending)
            self._link_local_images()
            state.assets = list(self._asset_index.values())
            self._checkpoint(state, force=True)

    def _link_local_images(self) -> None:
        """Point each listing at the local files downloaded so far (incremental,
        so partial/crash listings.json already reference the saved images)."""
        amap = {a.url: a.local_path for a in self._asset_index.values() if a.local_path}
        if not amap:
            return
        for lst in self._listings:
            for url in lst.image_urls:
                lp = amap.get(url)
                if lp and lp not in lst.local_image_paths:
                    lst.local_image_paths.append(lp)

    def _persist(self, state: CrawlState, logs: SiteLoggers) -> None:
        """Write a resume snapshot (listings + details) and checkpoint state."""
        from webarchiver.state import resume
        self._link_local_images()
        state.assets = list(self._asset_index.values())
        try:
            resume.save_listings(self._listings, self._resume_dir / "listings_state.json")
            resume.save_details(self._details, self._resume_dir / "details.json")
        except Exception as exc:  # noqa: BLE001
            logs.error.warning("resume snapshot failed: %s", exc)
        self._checkpoint(state, force=True)
        self._write_partial_listings(logs)

    async def _crawl_graphql(self, engine: object, state: CrawlState, logs: SiteLoggers) -> None:
        """Paginate allhomes sold history via its GraphQL persisted query."""
        from webarchiver.listings.allhomes_graphql import (
            DEFAULT_HASH, build_body, graphql_endpoint, parse_response, slug_from_url,
        )
        from webarchiver.listings.realestate_parse import map_allhomes_sold

        slug = slug_from_url(self._site.url)
        if not slug:
            logs.error.warning("GraphQL pager: no /research/<slug> in URL; skipping")
            return
        sha = self._site.graphql_hash or DEFAULT_HASH
        size = self._site.graphql_page_size or 10
        post_url = graphql_endpoint(self._site.url)
        done = set(state.extra.get("graphql_done", []))
        logs.crawl.info("GraphQL pager: %s, up to %d pages (%d already done)",
                        slug, self._site.max_pages, len(done))
        # One page per POST so we can checkpoint + download images after each.
        for p in range(1, self._site.max_pages + 1):
            if p in done:
                continue
            body = build_body(slug, p, size, sha)
            texts = await engine.browser_post_many(
                self._site.url, post_url, [body], wait_each=0.5)
            text = texts[0] if texts else ""
            nodes, _total, err = parse_response(text)
            if err:
                logs.error.warning("GraphQL page %d: %s", p, err)
                if err == "PersistedQueryNotFound":
                    logs.error.warning("Persisted-query hash rejected; site likely rotated it.")
                    break
                continue
            if not nodes:
                if p > 1:
                    break  # ran past the last page
                continue
            recs = [_record_from_map(map_allhomes_sold(nd), self._site.url) for nd in nodes]
            self._collect_listings(recs, f"graphql page {p}", logs)
            done.add(p); state.extra["graphql_done"] = sorted(done)
            self._persist(state, logs)

    async def _crawl_search_api(self, engine: object, state: CrawlState, logs: SiteLoggers) -> None:
        """Paginate the allhomes for-sale search by URL (?...&page=N).

        Each page is fetched as normal HTML and its listings are read from the
        embedded APP_PROPS store (the same proven parser used for page 1). This
        is more robust than the /wsvc/search JSON API, whose response envelope
        differs from the page hydration. One page per fetch so images download
        and state checkpoints after each.
        """
        from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit
        from webarchiver.listings.realestate_parse import (
            map_allhomes_search_item, parse_allhomes_search,
        )

        def page_url(n: int) -> str:
            parts = urlsplit(self._site.url)
            q = parse_qs(parts.query)
            q["page"] = [str(n)]
            return urlunsplit((parts.scheme, parts.netloc, parts.path,
                               urlencode(q, doseq=True), parts.fragment))

        res = await engine.fetch(self._site.url)
        if not getattr(res, "ok", False):
            logs.error.warning("search: page-1 fetch failed: %s", getattr(res, "error", ""))
            return
        meta = parse_allhomes_search(res.html)
        if not meta.get("items"):
            logs.error.warning("search: no initialSearchResults on page 1")
            return
        total = min(meta.get("total_pages") or 1, self._site.max_pages)
        done = set(state.extra.get("search_done", []))
        logs.crawl.info("search (URL pagination): %d total pages (capped %d, %d done)",
                        meta.get("total_pages"), total, len(done))
        for p in range(1, total + 1):
            if p in done:
                continue
            if p == 1:
                items = meta["items"]  # already in the page-1 HTML we fetched
            else:
                r = await engine.fetch(page_url(p))
                if not getattr(r, "ok", False):
                    logs.error.warning("search page %d fetch failed: %s",
                                       p, getattr(r, "error", ""))
                    continue
                items = parse_allhomes_search(r.html).get("items") or []
            if not items:
                logs.error.warning("search page %d: no listings found", p)
                if p > 1:
                    continue
            recs = [_record_from_map(map_allhomes_search_item(it), self._site.url)
                    for it in items]
            self._collect_listings(recs, f"search page {p}", logs)
            done.add(p); state.extra["search_done"] = sorted(done)
            self._persist(state, logs)

    async def _crawl_hash_pages(self, engine: object, state: CrawlState, logs: SiteLoggers) -> None:
        """Drive SPA hash pagination: one load, walk #1..#max_pages, extract each."""
        wait_each = max(2.0, self._app.global_config.page_load_wait / 3.0)
        results = await engine.fetch_hash_pages(self._site.url, self._site.max_pages, wait_each)
        logs.crawl.info("Hash pagination captured %d pages", len(results))
        for fr in results:
            rec = PageRecord(url=fr.url, final_url=fr.final_url,
                             status_code=fr.status_code, depth=0)
            await self._on_page(rec, fr.html, state, logs)

    async def _crawl_details(self, engine: object, state: CrawlState, logs: SiteLoggers) -> None:
        """Visit each property page, extract full detail + gallery.

        Source-aware: allhomes uses the embedded APP_PROPS store, realestate
        reads its detail-page DOM markers. Extracted extras are also merged back
        into the matching listing record so listings.csv/json carry them.
        """
        from webarchiver.listings.allhomes_detail import parse_allhomes_detail
        from webarchiver.listings.realestate_detail import parse_realestate_detail

        src = (self._site.listing_source or "").lower()
        host = self._workspace_host().lower()
        is_re = src == "realestate" or "realestate" in host
        parse = parse_realestate_detail if is_re else parse_allhomes_detail

        by_url = {l.listing_url: l for l in self._listings if l.listing_url}
        urls, seen = [], set()
        for lst in self._listings:
            u = lst.listing_url
            if u and u not in seen:
                seen.add(u); urls.append(u)
        lim = self._site.fetch_details_limit
        if lim and lim > 0:
            urls = urls[:lim]
        logs.crawl.info("Detail crawl (%s): visiting %d property pages",
                        "realestate" if is_re else "allhomes", len(urls))
        done = set(state.extra.get("details_done", []))
        for i, u in enumerate(urls, 1):
            if u in done:
                continue
            try:
                res = await engine.fetch(u)
            except Exception as exc:  # noqa: BLE001
                logs.error.warning("detail fetch error %s: %s", u, exc); continue
            if not getattr(res, "ok", False):
                logs.error.warning("detail fetch failed %s -> %s", u, getattr(res, "error", "")); continue
            d = parse(res.html, u)
            if not d:
                logs.error.warning("detail parse empty %s", u); continue
            if self._site.download_images:
                for im in d.get("image_urls", []):
                    rec = self._asset_index.get(im)
                    if rec is None:
                        rec = AssetRecord(url=im, asset_type=AssetType.IMAGE)
                        self._asset_index[im] = rec
                    rec.referenced_by.add(u)
            lst = by_url.get(u)
            if lst is not None:
                self._merge_detail_into_record(lst, d, is_re)
            self._details.append(d)
            done.add(u); state.extra["details_done"] = sorted(done)
            if i % 10 == 0 or i == len(urls):
                self._persist(state, logs)
                logs.crawl.info("  detail %d/%d", i, len(urls))
            # Human-like jittered pause between property pages (anti-block).
            delay = self._app.global_config.detail_delay_sec
            if delay > 0:
                import random
                await asyncio.sleep(delay + random.uniform(0, delay))

    @staticmethod
    def _merge_detail_into_record(rec: ListingRecord, d: dict, is_re: bool) -> None:
        """Copy the newly scraped extras from a detail dict onto the listing row."""
        pv = d.get("page_views")
        if pv not in (None, ""):
            rec.page_views = str(pv)
        feats = d.get("property_features") or d.get("features_list")
        if feats:
            rec.property_features = "; ".join(str(f) for f in feats)
        if d.get("description") and not rec.description:
            rec.description = d["description"]
        if is_re:
            if d.get("property_id"):
                rec.property_id = str(d["property_id"])
            return
        # --- allhomes-specific extras ---
        if d.get("listing_id"):
            rec.listing_id = str(d["listing_id"])
        if d.get("last_updated"):
            rec.last_updated = str(d["last_updated"])
        if d.get("listed_date") and not rec.listed_date:
            rec.listed_date = str(d["listed_date"])
        if d.get("suburb") and not rec.suburb:
            rec.suburb = d["suburb"]
        agents = d.get("agents") or []
        if agents:
            rec.agent_photo = agents[0].get("profile_photo", "")
            if not rec.agent:
                rec.agent = agents[0].get("name", "")
            if not rec.agents:
                rec.agents = "; ".join(
                    f"{a.get('name','')} ({a.get('phone','')})".strip() for a in agents
                )
        agency = d.get("agency") or {}
        if agency:
            rec.agency_logo = agency.get("logo", "")
            if not rec.agency:
                rec.agency = agency.get("name", "")
        insp = d.get("inspections") or []
        if insp and not rec.inspections:
            rec.inspections = "; ".join(
                f"{i.get('startTime','')}-{i.get('endTime','')}".strip("-") for i in insp
            )
        auc = d.get("auctions") or []
        if auc and not rec.auction:
            a0 = auc[0]
            rec.auction = f"{a0.get('startTime','')} {a0.get('venue','')}".strip()

    def _finalise_details(self, assets: list[AssetRecord], logs: SiteLoggers) -> None:
        """Link downloaded image paths into details and write details.json."""
        from webarchiver.report.exporters import to_json
        amap = {a.url: a.local_path for a in assets if a.local_path}
        for d in self._details:
            d["local_image_paths"] = [amap[u] for u in d.get("image_urls", []) if u in amap]
        to_json(self._details, self._workspace.root / "details.json")
        logs.crawl.info("Wrote details.json (%d properties, full sections)", len(self._details))

    def _register_listing_images(self, listings: list[ListingRecord]) -> None:
        """Queue each listing's photos for download (so we get property photos)."""
        for lst in listings:
            for url in list(lst.image_urls) + list(lst.floorplan_urls):
                existing = self._asset_index.get(url)
                if existing is None:
                    existing = AssetRecord(url=url, asset_type=AssetType.IMAGE)
                    self._asset_index[url] = existing
                existing.referenced_by.add(lst.source_page)

    # Embedded players (YouTube/Vimeo/Matterport) are not downloadable files;
    # only direct media files are queued. The links are still kept in the CSV.
    _EMBED_HOSTS = ("youtube.com", "youtu.be", "vimeo.com", "matterport.com")

    def _register_listing_videos(self, listings: list[ListingRecord]) -> None:
        for lst in listings:
            for url in lst.video_urls:
                if any(h in url.lower() for h in self._EMBED_HOSTS):
                    continue
                existing = self._asset_index.get(url)
                if existing is None:
                    existing = AssetRecord(url=url, asset_type=AssetType.VIDEO)
                    self._asset_index[url] = existing
                existing.referenced_by.add(lst.source_page)

    def _write_partial_listings(self, logs: SiteLoggers) -> None:
        if not self._listings:
            return
        # JSON + CSV every checkpoint (cheap); the heavier Excel workbook every
        # 10th checkpoint so an interrupted/blocked run still leaves a .xlsx
        # without rebuilding it on every single checkpoint.
        self._partial_count = getattr(self, "_partial_count", 0) + 1
        want_xlsx = self._site.export_excel and (self._partial_count % 10 == 1)
        write_listings(
            self._listings, self._workspace.root,
            export_csv=self._site.export_csv,
            export_excel=want_xlsx,
            export_json=self._site.export_json,
            logger=logs.crawl,
        )

    # ---- phases -------------------------------------------------------
    async def _download_assets(self, engine, logs: SiteLoggers) -> list[AssetRecord]:
        from webarchiver.download.asset_downloader import AssetDownloader

        pending = [a for a in self._asset_index.values() if not a.downloaded]
        if not pending:
            return list(self._asset_index.values())
        downloader = AssetDownloader(
            engine=engine, workspace=self._workspace,
            concurrency=self._app.global_config.concurrency, logger=logs.download,
        )
        await downloader.download_all(pending)
        return list(self._asset_index.values())

    def _finalise_listings(self, assets: list[AssetRecord], logs: SiteLoggers) -> None:
        # Link each listing's image to the file the asset downloader already stored.
        asset_map = {a.url: a.local_path for a in assets if a.local_path}
        for lst in self._listings:
            for url in lst.image_urls:
                if url in asset_map and asset_map[url] not in lst.local_image_paths:
                    lst.local_image_paths.append(asset_map[url])

        gc = self._app.global_config
        export = self._listings
        if gc.validate_before_save:
            accepted, results = validate_all(
                self._listings,
                min_quality_score=gc.min_quality_score,
                auto_fix=gc.auto_fix_data,
            )
            export = accepted
            self._listings_rejected = len(self._listings) - len(accepted)
            if results:
                self._avg_quality = sum(r.score for r in results) / len(results)
            self._write_quality_report(results, logs)
            logs.crawl.info(
                "Validation: %d accepted, %d rejected (avg quality %.0f/100)",
                len(accepted), self._listings_rejected, self._avg_quality,
            )

        write_listings(
            export, self._workspace.root,
            export_csv=self._site.export_csv,
            export_excel=self._site.export_excel,
            export_json=self._site.export_json,
            logger=logs.crawl,
        )

    def _write_quality_report(self, results: list, logs: SiteLoggers) -> None:
        from webarchiver.report.exporters import to_csv
        rows = [
            {
                "Address": r.record.address,
                "Quality Score": r.score,
                "Rejected": "yes" if r.rejected else "no",
                "Errors": "; ".join(r.errors),
                "Warnings": "; ".join(r.warnings),
                "Auto-fixes": "; ".join(r.fixes),
            }
            for r in results
        ]
        to_csv(rows, self._workspace.root / "metadata" / "listings_quality.csv")

    def _write_proxy_health(self, engine: object, logs: SiteLoggers) -> None:
        manager = getattr(engine, "proxy_manager", None)
        if manager is None:
            return
        try:
            from webarchiver.report.exporters import to_json
            report = manager.get_health_report()
            to_json(report, self._workspace.root / "metadata" / "proxy_health.json")
            if report.get("enabled"):
                logs.crawl.info(
                    "Proxy health: %d rotations, %d ok, %d blocked, %d tunnel-fail",
                    report["rotations"], report["successful_requests"],
                    report["blocked_requests"], report["tunnel_failures"],
                )
        except Exception:  # noqa: BLE001
            pass

    def _write_crawl_report_only(self, summary: SiteSummary, logs: SiteLoggers) -> None:
        from webarchiver.report.exporters import to_csv, to_excel

        rows = [summary.as_row()]
        if self._site.export_csv:
            to_csv(rows, self._workspace.root / "crawl_report.csv")
        if self._site.export_excel:
            to_excel({"summary": rows}, self._workspace.root / "crawl_report.xlsx")
        logs.crawl.info("Listings mode: wrote crawl_report + listings (no full archive)")

    async def _capture_screenshots(self, engine, pages, logs: SiteLoggers) -> dict[str, str]:
        if not self._site.take_screenshots:
            return {}
        service = ScreenshotService(engine=engine, workspace=self._workspace, logger=logs.crawl)
        return await service.capture_all(pages)

    def _rewrite_for_offline(self, pages, assets, logs: SiteLoggers) -> None:
        page_map = {p.final_url or p.url: p.local_path for p in pages if p.local_path}
        # Also index by the original URL so links written with the request URL resolve.
        for p in pages:
            if p.local_path:
                page_map.setdefault(p.url, p.local_path)
        asset_map = {a.url: a.local_path for a in assets if a.local_path}
        rewriter = LinkRewriter(page_map, asset_map)

        count = 0
        for page in pages:
            if not page.local_path:
                continue
            abs_path = self._workspace.root / page.local_path
            try:
                html = abs_path.read_text(encoding="utf-8", errors="replace")
                new_html = rewriter.rewrite(
                    html, base_url=page.final_url or page.url, page_relpath=page.local_path
                )
                abs_path.write_text(new_html, encoding="utf-8", errors="replace")
                count += 1
            except OSError as exc:
                logs.error.warning("Rewrite failed for %s: %s", page.url, exc)
        logs.crawl.info("Rewrote %d pages for offline browsing", count)

    # ---- helpers ------------------------------------------------------
    def _populate_summary(self, summary, pages, assets, broken, screenshots) -> None:
        summary.pages_discovered = len(pages)
        summary.pages_downloaded = sum(1 for p in pages if p.downloaded)
        summary.pages_failed = sum(1 for p in pages if p.error)
        summary.blocked_pages = sum(1 for p in pages if p.error and p.error.startswith('blocked'))
        by_type = {t: 0 for t in AssetType}
        seen_hashes: set[str] = set()
        for a in assets:
            if a.downloaded:
                by_type[a.asset_type] += 1
                summary.total_size_bytes += a.file_size
                if a.content_hash in seen_hashes:
                    summary.duplicate_assets += 1
                seen_hashes.add(a.content_hash)
            elif a.error:
                summary.assets_failed += 1
        summary.images_downloaded = by_type[AssetType.IMAGE]
        summary.videos_downloaded = by_type[AssetType.VIDEO]
        summary.documents_downloaded = by_type[AssetType.DOCUMENT]
        summary.css_downloaded = by_type[AssetType.CSS]
        summary.js_downloaded = by_type[AssetType.JS]
        summary.fonts_downloaded = by_type[AssetType.FONT]
        summary.broken_links = len(broken)
        summary.screenshots = len(screenshots)
        summary.listings_extracted = len(self._listings)
        summary.listings_rejected = self._listings_rejected
        summary.avg_quality_score = round(self._avg_quality, 1)
        summary.external_domains = len({
            urlsplit(link).hostname for p in pages for link in p.external_links
        } - {None})

    def _workspace_host(self) -> str:
        from webarchiver.core.url_utils import registrable_host

        return registrable_host(self._site.url)

    def _load_or_new_state(self) -> CrawlState:
        if self._resume:
            existing = CrawlState.load(self._state_path)
            if existing is not None:
                return existing
        return CrawlState(site_name=self._site.name, site_url=self._site.url)

    def _force_rerun(self) -> bool:
        return not self._resume

    def _checkpoint(self, state: CrawlState, *, force: bool = False) -> None:
        if force or self._since_checkpoint >= _CHECKPOINT_EVERY:
            state.save(self._state_path)
            self._since_checkpoint = 0
