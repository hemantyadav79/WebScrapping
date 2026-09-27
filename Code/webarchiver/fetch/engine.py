"""Render-engine abstraction.

The crawler depends only on the :class:`RenderEngine` protocol, never on a
concrete browser library. This is the seam that lets DrissionPage be swapped
for Playwright (or anything else) without touching crawl logic — satisfying the
Dependency Inversion principle.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field


@dataclass(slots=True)
class FetchResult:
    """Outcome of fetching a single URL."""

    url: str
    final_url: str
    status_code: int
    content_type: str = ""
    html: str = ""
    body: bytes = b""
    redirect_chain: list[str] = field(default_factory=list)
    elapsed: float = 0.0
    error: str | None = None

    @property
    def ok(self) -> bool:
        """True when the fetch succeeded with a 2xx status and no error."""
        return self.error is None and 200 <= self.status_code < 300

    @property
    def is_html(self) -> bool:
        ct = self.content_type.lower()
        return "html" in ct or (not ct and bool(self.html))


class RenderEngine(abc.ABC):
    """Abstract page-fetch engine. Implementations may be sync or async."""

    @abc.abstractmethod
    async def start(self) -> None:
        """Acquire resources (sessions, browser processes)."""

    @abc.abstractmethod
    async def fetch(self, url: str) -> FetchResult:
        """Fetch ``url`` and return a :class:`FetchResult`."""

    @abc.abstractmethod
    async def fetch_binary(self, url: str) -> FetchResult:
        """Fetch a binary asset at ``url`` (no rendering)."""

    @abc.abstractmethod
    async def screenshot(self, url: str, dest_path: str) -> bool:
        """Capture a full-page screenshot to ``dest_path``; True on success."""

    @abc.abstractmethod
    async def close(self) -> None:
        """Release all resources."""

    async def fetch_hash_pages(self, url: str, count: int, wait_each: float = 3.0) -> list:
        """Load ``url`` once and step through ``#1..#count`` hash pages, returning
        a FetchResult per page. Default: unsupported (only the browser engine)."""
        return []

    async def browser_post_many(self, page_url: str, post_url: str, bodies: list,
                                wait_each: float = 0.6) -> list:
        """Load ``page_url`` once, then POST each JSON body to ``post_url`` from
        inside the page (so auth/bot headers apply). Returns response texts.
        Default: unsupported (only the browser engine)."""
        return []

    async def __aenter__(self) -> "RenderEngine":
        await self.start()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()
