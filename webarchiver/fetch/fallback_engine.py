"""Fallback render engine: try a primary engine, fall back to a secondary.

Used to run normal DrissionPage as the primary, and DrissionPage pointed at the
CloakBrowser binary as the backup: if a normal fetch fails (error, non-2xx,
empty, blocked), the same URL is retried with the stealth browser. The backup is
started lazily — the second browser only launches the first time it's needed.
"""
from __future__ import annotations

import logging

from webarchiver.fetch.engine import FetchResult, RenderEngine


def _usable(res: FetchResult, *, binary: bool) -> bool:
    if res is None or not res.ok:
        return False
    return bool(res.body) if binary else bool(res.html or res.body)


class FallbackRenderEngine(RenderEngine):
    """Delegate to ``primary``; on failure, retry with ``fallback``."""

    def __init__(self, primary: RenderEngine, fallback: RenderEngine,
                 logger: logging.Logger | None = None) -> None:
        self._primary = primary
        self._fallback = fallback
        self._fallback_started = False
        self._log = logger or logging.getLogger("webarchiver.fallback")
        self.proxy_manager = (getattr(primary, "proxy_manager", None)
                              or getattr(fallback, "proxy_manager", None))

    async def start(self) -> None:
        await self._primary.start()

    async def _ensure_fallback(self) -> None:
        if not self._fallback_started:
            self._log.info("Starting CloakBrowser fallback engine")
            await self._fallback.start()
            self._fallback_started = True

    async def close(self) -> None:
        try:
            await self._primary.close()
        finally:
            if self._fallback_started:
                await self._fallback.close()

    async def fetch(self, url: str) -> FetchResult:
        res = await self._primary.fetch(url)
        if _usable(res, binary=False):
            return res
        self._log.warning("Primary fetch failed (%s) -> CloakBrowser fallback: %s",
                          res.error or f"HTTP {res.status_code}", url)
        try:
            await self._ensure_fallback()
            return await self._fallback.fetch(url)
        except Exception as exc:  # noqa: BLE001 - if cloak can't start, keep primary's result
            self._log.warning("CloakBrowser fallback unavailable (%s)", exc)
            return res

    async def fetch_binary(self, url: str) -> FetchResult:
        res = await self._primary.fetch_binary(url)
        if _usable(res, binary=True):
            return res
        try:
            await self._ensure_fallback()
            return await self._fallback.fetch_binary(url)
        except Exception:  # noqa: BLE001
            return res

    async def screenshot(self, url: str, dest_path: str) -> bool:
        try:
            return await self._primary.screenshot(url, dest_path)
        except Exception:  # noqa: BLE001
            return False

    async def fetch_hash_pages(self, url: str, count: int, wait_each: float = 3.0) -> list:
        return await self._primary.fetch_hash_pages(url, count, wait_each)

    async def browser_post_many(self, page_url: str, post_url: str, bodies: list,
                                wait_each: float = 0.6) -> list:
        return await self._primary.browser_post_many(page_url, post_url, bodies, wait_each)
