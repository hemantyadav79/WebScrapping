"""Breadth-first, concurrency-bounded website crawler.

The crawler is engine-agnostic: it drives any :class:`RenderEngine`. For each
page it fetches, it records status, redirects and extracted metadata, then
enqueues newly discovered same-domain links until ``max_depth`` / ``max_pages``
are reached. Completed pages are streamed to an ``on_page`` callback so the
pipeline can persist HTML immediately (supporting resume).

Pagination links (``?page=2``, ``/page/3``, ``rel=next`` …) are enqueued at the
*same* depth as the page that referenced them, so every page of a paginated
listing is traversed even when ``max_depth`` would otherwise stop the walk.
An optional LLM link filter (Ollama) can veto junk/ad links before they are
enqueued; pagination links are always kept.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from webarchiver.config.models import WebsiteConfig
from webarchiver.core.url_utils import normalize_url, registrable_host
from webarchiver.crawl.frontier import Frontier
from webarchiver.crawl.robots import RobotsPolicy
from webarchiver.extract.models import BrokenLink, PageRecord
from webarchiver.extract.page_parser import PageParser
from webarchiver.fetch.engine import RenderEngine

OnPage = Callable[[PageRecord, str], Awaitable[None]]


@dataclass(slots=True)
class CrawlOutcome:
    """Aggregate result of a crawl."""

    pages: list[PageRecord] = field(default_factory=list)
    broken_links: list[BrokenLink] = field(default_factory=list)
    pages_failed: int = 0


class Crawler:
    """Concurrency-bounded BFS crawler for a single website."""

    def __init__(
        self,
        *,
        site: WebsiteConfig,
        engine: RenderEngine,
        parser: PageParser,
        frontier: Frontier,
        robots: RobotsPolicy,
        concurrency: int,
        logger: logging.Logger | None = None,
        error_logger: logging.Logger | None = None,
        link_filter: object | None = None,
        explicit_pages: list[str] | None = None,
    ) -> None:
        self._site = site
        self._explicit_pages = explicit_pages
        self._engine = engine
        self._parser = parser
        self._frontier = frontier
        self._robots = robots
        self._concurrency = max(1, concurrency)
        self._log = logger or logging.getLogger("webarchiver.crawl")
        self._errlog = error_logger or self._log
        self._root_host = registrable_host(site.url)
        self._link_filter = link_filter  # optional OllamaLinkFilter (duck-typed)

    def _path_allowed(self, url: str) -> bool:
        path = urlsplit(url).path or "/"
        if self._site.excluded_paths and any(path.startswith(p) for p in self._site.excluded_paths):
            return False
        if self._site.allowed_paths and not any(path.startswith(p) for p in self._site.allowed_paths):
            return False
        return True

    def _next_batch(self, remaining: int) -> list[tuple[str, int]]:
        """Pop up to ``min(concurrency, remaining)`` tasks off the frontier."""
        batch: list[tuple[str, int]] = []
        limit = min(self._concurrency, remaining)
        while len(batch) < limit:
            task = self._frontier.pop()
            if task is None:
                break
            batch.append((task.url, task.depth))
        return batch

    async def crawl(self, on_page: OnPage) -> CrawlOutcome:
        """Run the crawl, invoking ``on_page`` for each fetched page."""
        outcome = CrawlOutcome()
        if self._explicit_pages:
            for page_url in self._explicit_pages:          # ordered list-1..list-N
                self._frontier.add(normalize_url(page_url), 0)
            self._log.info("Pagination plan: %d ordered pages", len(self._explicit_pages))
        else:
            self._frontier.add(normalize_url(self._site.url), 0)

        while True:
            remaining = self._site.max_pages - len(outcome.pages)
            if remaining <= 0:
                break
            batch = self._next_batch(remaining)
            if not batch:
                break

            results = await asyncio.gather(*(self._process(u, d) for u, d in batch))
            for record, html in results:
                await self._absorb(record, html, outcome, on_page)

        self._log.info(
            "Crawl finished: %d pages (%d failed), %d visited",
            len(outcome.pages), outcome.pages_failed, self._frontier.visited_count,
        )
        return outcome

    async def _absorb(
        self, record: PageRecord, html: str, outcome: CrawlOutcome, on_page: OnPage
    ) -> None:
        if record.error:
            outcome.pages_failed += 1
            outcome.broken_links.append(
                BrokenLink(
                    source_page=record.url,
                    broken_url=record.url,
                    status_code=record.status_code,
                    error_message=record.error,
                )
            )
        outcome.pages.append(record)
        await on_page(record, html)

        if record.error:
            return

        if self._explicit_pages is not None:
            return  # explicit pagination plan: do not follow any discovered links

        pagination = set(record.pagination_links) if self._site.follow_pagination else set()
        candidates = [
            link for link in record.internal_links
            if self._path_allowed(link) and self._robots.can_fetch(link)
        ]
        if self._link_filter is not None and getattr(self._link_filter, "enabled", False):
            kept = await self._link_filter.filter_links(candidates)
            # Pagination links are navigation, never ads: always keep them.
            kept_set = set(kept) | (set(candidates) & pagination)
            candidates = [link for link in candidates if link in kept_set]

        for link in candidates:
            if link in pagination:
                # Pagination continues the same listing: do not consume depth,
                # so page 2..N are always traversed even at max_depth.
                self._frontier.add(link, record.depth)
            elif record.depth < self._site.max_depth:
                self._frontier.add(link, record.depth + 1)

    async def _process(self, url: str, depth: int) -> tuple[PageRecord, str]:
        self._log.debug("Fetching [d=%d] %s", depth, url)
        result = await self._engine.fetch(url)

        if not result.ok:
            self._errlog.warning("Fetch failed %s -> %s", url, result.error or result.status_code)
            rec = PageRecord(
                url=url, final_url=result.final_url or url,
                status_code=result.status_code, depth=depth,
                redirect_chain=result.redirect_chain,
                error=result.error or f"HTTP {result.status_code}",
            )
            return rec, ""

        record = self._parser.parse(result.html, result.final_url or url)
        record.url = url
        record.final_url = result.final_url or url
        record.status_code = result.status_code
        record.depth = depth
        record.redirect_chain = result.redirect_chain
        return record, result.html
