"""Static HTTP render engine backed by aiohttp.

This is the fast path for server-rendered sites and for downloading binary
assets. It honours a shared rate limiter, request timeout, retry policy and a
configurable User-Agent. It performs no JavaScript execution — for that use the
DrissionPage engine.

``aiohttp`` is imported lazily inside :meth:`start` so the rest of the platform
(and its tests) can be imported even in environments where aiohttp is absent.
"""
from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from webarchiver.core.ratelimit import AsyncRateLimiter, retry_async
from webarchiver.crawl.blockdetect import is_blocked
from webarchiver.fetch.engine import FetchResult, RenderEngine

if TYPE_CHECKING:  # pragma: no cover
    from webarchiver.config.models import GlobalConfig

# Status codes worth retrying (transient server / rate-limit errors).
_RETRY_STATUS = {408, 429, 500, 502, 503, 504}


class _RetryableStatus(Exception):
    def __init__(self, status: int) -> None:
        super().__init__(f"retryable status {status}")
        self.status = status


class HttpRenderEngine(RenderEngine):
    """aiohttp-based engine for static pages and binary assets."""

    def __init__(self, config: "GlobalConfig", proxy_manager: Any = None) -> None:
        self._config = config
        self._limiter = AsyncRateLimiter(config.rate_limit_per_sec)
        self._session: Any = None
        self._aiohttp: Any = None
        self.proxy_manager = proxy_manager
        self._extra_headers: dict[str, str] = {}
        self._referer: str = ""
        self._cookie_header: str = ""
        self._cookie_host: str = ""

    def set_session(self, *, referer: str = "", cookie_header: str = "", cookie_host: str = "") -> None:
        """Browser-derived session info applied to downloads (Referer always; the
        Cookie only on the matching registrable domain, never leaked to CDNs)."""
        self._referer = referer or ""
        self._cookie_header = cookie_header or ""
        self._cookie_host = (cookie_host or "").lower()

    def _headers_for(self, url: str) -> dict:
        from urllib.parse import urlsplit
        hdrs = dict(self._extra_headers)
        if self._referer:
            hdrs.setdefault("Referer", self._referer)
        if self._cookie_header and self._cookie_host:
            host = (urlsplit(url).hostname or "").lower()
            base = self._cookie_host.split(".", 1)[1] if self._cookie_host.count(".") >= 2 else self._cookie_host
            if host == self._cookie_host or host == base or host.endswith("." + base):
                hdrs["Cookie"] = self._cookie_header
        return hdrs or None

    def _current_proxy(self) -> str | None:
        if self.proxy_manager is not None:
            return self.proxy_manager.current()
        return self._config.proxy or None

    def set_extra_headers(self, headers: dict[str, str]) -> None:
        """Merge extra headers (e.g. Cookie, Referer) into every request."""
        self._extra_headers = dict(headers or {})

    async def start(self) -> None:
        if self._session is not None:
            return
        try:
            import aiohttp
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "aiohttp is required for the HTTP render engine. Install it with "
                "`pip install aiohttp`."
            ) from exc
        self._aiohttp = aiohttp
        timeout = aiohttp.ClientTimeout(total=self._config.request_timeout)
        connector = aiohttp.TCPConnector(limit=self._config.concurrency * 2, ssl=False)
        self._session = aiohttp.ClientSession(
            timeout=timeout, connector=connector,
            headers={"User-Agent": self._config.user_agent},
        )

    async def _request(self, url: str, *, read_text: bool) -> FetchResult:
        if self._session is None:
            raise RuntimeError("HttpRenderEngine.start() must be called before use")
        aiohttp = self._aiohttp
        await self._limiter.acquire()
        start = time.monotonic()

        async def _do() -> FetchResult:
            async with self._session.get(
                url,
                allow_redirects=True,
                proxy=self._current_proxy(),
                headers=self._headers_for(url),
            ) as resp:
                if resp.status in _RETRY_STATUS:
                    raise _RetryableStatus(resp.status)
                redirect_chain = [str(h.url) for h in resp.history]
                content_type = resp.headers.get("Content-Type", "")
                body = await resp.read()
                html = ""
                if read_text and "html" in content_type.lower():
                    html = body.decode(resp.get_encoding() or "utf-8", errors="replace")
                return FetchResult(
                    url=url, final_url=str(resp.url), status_code=resp.status,
                    content_type=content_type, html=html,
                    body=b"" if read_text else body,
                    redirect_chain=redirect_chain, elapsed=time.monotonic() - start,
                )

        try:
            return await retry_async(
                _do, retries=self._config.max_retries, backoff=self._config.retry_backoff,
                exceptions=(aiohttp.ClientError, _RetryableStatus, TimeoutError),
            )
        except _RetryableStatus as exc:
            return FetchResult(
                url=url, final_url=url, status_code=exc.status,
                elapsed=time.monotonic() - start, error=f"HTTP {exc.status} after retries",
            )
        except (aiohttp.ClientError, TimeoutError) as exc:
            if self.proxy_manager is not None:
                self.proxy_manager.report_failure()
            return FetchResult(
                url=url, final_url=url, status_code=0,
                elapsed=time.monotonic() - start, error=f"{type(exc).__name__}: {exc}",
            )

    async def fetch(self, url: str) -> FetchResult:
        attempts = self._config.block_max_retries if self._config.block_detection else 0
        result = await self._request(url, read_text=True)
        for _ in range(attempts):
            if result.error:
                break
            blocked, reason = is_blocked(
                result.html, result.status_code, min_size_bytes=self._config.block_min_size_bytes
            )
            if not blocked:
                break
            if self.proxy_manager is not None:
                self.proxy_manager.report_failure(blocked=True)  # rotating -> fresh IP next call
            result = await self._request(url, read_text=True)
        if not result.error and self._config.block_detection:
            blocked, reason = is_blocked(
                result.html, result.status_code, min_size_bytes=self._config.block_min_size_bytes
            )
            if blocked:
                if self.proxy_manager is not None:
                    self.proxy_manager.report_failure(blocked=True)
                result.error = f"blocked: {reason}"
        if not result.error and self.proxy_manager is not None:
            self.proxy_manager.report_success()
        return result

    async def fetch_binary(self, url: str) -> FetchResult:
        return await self._request(url, read_text=False)

    async def screenshot(self, url: str, dest_path: str) -> bool:
        return False  # Screenshots require a real browser.

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None
