"""DrissionPage render engine for JavaScript-heavy websites.

DrissionPage drives a real Chromium instance, so it renders client-side
content, handles lazy loading and can capture full-page screenshots. The
library is synchronous, so blocking calls are offloaded to a worker thread via
``asyncio.to_thread`` to stay compatible with the async crawler.

Binary asset downloads are delegated to a composed :class:`HttpRenderEngine`,
because pulling raw bytes through a browser is slow and unnecessary.
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from webarchiver.core.exceptions import RenderEngineError
from webarchiver.crawl.blockdetect import is_blocked
from webarchiver.fetch.engine import FetchResult, RenderEngine
from webarchiver.fetch.http_fetcher import HttpRenderEngine

if TYPE_CHECKING:
    from webarchiver.config.models import GlobalConfig


class DrissionRenderEngine(RenderEngine):
    """Chromium-backed engine using DrissionPage for full rendering."""

    def __init__(self, config: "GlobalConfig", *, headless: bool = True, proxy_manager: Any = None,
                 browser_path: str = "") -> None:
        self._config = config
        self._headless = headless
        self._browser_path = browser_path  # point at a custom Chromium (e.g. CloakBrowser)
        self._page: Any = None
        self.proxy_manager = proxy_manager
        self._binary = HttpRenderEngine(config, proxy_manager)
        self._lock = asyncio.Lock()  # DrissionPage page object is not concurrency-safe.
        self._cookies_synced = False

    async def start(self) -> None:
        await self._binary.start()
        if self._page is not None:
            return
        try:
            from DrissionPage import ChromiumOptions, ChromiumPage
        except ImportError as exc:  # pragma: no cover - depends on local env
            raise RenderEngineError(
                "DrissionPage is not installed. Run `pip install DrissionPage` "
                "or set render_engine to 'http'."
            ) from exc

        def _build() -> Any:
            cfg = self._config
            # 1) Attach to an already-running Chrome (you opened the site yourself
            #    and passed the bot check) -> reuse that human, logged-in session.
            addr = (cfg.chrome_debugger_address or "").strip()
            if addr:
                return ChromiumPage(addr_or_opts=addr)

            # 2) Otherwise launch Chrome. Use a persistent real profile if given
            #    (cookies/anti-bot tokens carry over between runs); else an
            #    isolated auto_port instance.
            opts = ChromiumOptions()
            udd = (cfg.chrome_user_data_dir or "").strip()
            if udd:
                opts.set_user_data_path(udd)
            else:
                opts.auto_port()
            if self._browser_path:
                try:
                    opts.set_browser_path(self._browser_path)
                except Exception:  # noqa: BLE001 - older DrissionPage API name
                    pass
            if self._headless:
                opts.headless(True)
            if sys.platform.startswith("linux"):
                opts.set_argument("--no-sandbox")  # only needed for root/containers
            opts.set_argument("--start-maximized")
            # The AutomationControlled flag shows a banner and is a bot tell; in
            # stealth mode we omit it so the browser looks like a normal one.
            if not cfg.stealth:
                opts.set_argument("--disable-blink-features=AutomationControlled")
            launch_proxy = (
                self.proxy_manager.current() if self.proxy_manager is not None
                else (cfg.proxy or "")
            )
            if launch_proxy:
                opts.set_proxy(launch_proxy)
            # Only override the UA when explicitly requested; a mismatched UA is a
            # fingerprint bot-protection can detect, so by default keep Chrome's own.
            if cfg.override_user_agent and cfg.user_agent:
                opts.set_user_agent(cfg.user_agent)
            return ChromiumPage(addr_or_opts=opts)

        try:
            self._page = await asyncio.to_thread(_build)
        except Exception as exc:  # pragma: no cover - depends on local env
            raise RenderEngineError(f"Failed to launch Chromium: {exc}") from exc

    async def fetch(self, url: str) -> FetchResult:
        if self._page is None:
            raise RuntimeError("DrissionRenderEngine.start() must be called before use")
        start = time.monotonic()

        def _load() -> FetchResult:
            page = self._page
            attempts = self._config.block_max_retries if self._config.block_detection else 0
            html = ""
            for i in range(attempts + 1):
                page.get(url, timeout=self._config.request_timeout)
                try:
                    page.wait(self._config.page_load_wait)  # let Kasada/JS settle
                    page.scroll.to_bottom()
                    page.wait(1.0)
                    page.scroll.to_top()
                    page.wait(0.5)
                except Exception:  # noqa: BLE001 - scrolling is best-effort
                    pass
                html = page.html or ""
                if not self._config.block_detection:
                    break
                blocked, _reason = is_blocked(
                    html, 200, min_size_bytes=self._config.block_min_size_bytes
                )
                if not blocked:
                    break
                if self.proxy_manager is not None:
                    self.proxy_manager.report_failure(blocked=True)
                if i < attempts:
                    page.wait(2.0)  # brief pause, then reload so the challenge token clears

            final_url = page.url or url
            has_content = bool(html.strip())
            blocked, reason = (
                is_blocked(html, 200, min_size_bytes=self._config.block_min_size_bytes)
                if self._config.block_detection else (False, "")
            )
            if blocked:
                error = f"blocked: {reason}"
            elif self.proxy_manager is not None and has_content:
                self.proxy_manager.report_success()
                error = None
            else:
                error = None if has_content else "empty document / navigation failed"
            return FetchResult(
                url=url,
                final_url=final_url,
                status_code=200 if (has_content and not blocked) else 0,
                content_type="text/html",
                html=html,
                elapsed=time.monotonic() - start,
                error=error,
            )

        async with self._lock:
            try:
                return await asyncio.to_thread(_load)
            except Exception as exc:  # noqa: BLE001
                return FetchResult(
                    url=url, final_url=url, status_code=0,
                    elapsed=time.monotonic() - start, error=str(exc),
                )

    async def fetch_binary(self, url: str) -> FetchResult:
        # Download via the aiohttp engine, carrying the browser's Referer + (host-
        # scoped) cookies so image CDNs serve the file instead of a 403.
        if not self._cookies_synced and self._page is not None:
            await self._sync_cookies()
        return await self._binary.fetch_binary(url)

    async def _sync_cookies(self) -> None:
        from urllib.parse import urlsplit

        def _read() -> tuple[str, str, str]:
            try:
                cookies = self._page.cookies(as_dict=True) or {}
                referer = self._page.url or ""
            except Exception:  # noqa: BLE001
                return "", "", ""
            cookie_header = "; ".join(f"{k}={v}" for k, v in cookies.items())
            host = (urlsplit(referer).hostname or "")
            return cookie_header, host, referer

        async with self._lock:
            cookie_header, host, referer = await asyncio.to_thread(_read)
        if not referer and host:
            referer = f"https://{host}/"
        self._binary.set_session(referer=referer, cookie_header=cookie_header, cookie_host=host)
        self._cookies_synced = True

    async def screenshot(self, url: str, dest_path: str) -> bool:
        if self._page is None:
            return False

        def _shot() -> bool:
            page = self._page
            if page.url != url:
                page.get(url, timeout=self._config.request_timeout)
            dest = Path(dest_path)
            page.get_screenshot(path=str(dest.parent), name=dest.name, full_page=True)
            return True

        async with self._lock:
            try:
                return await asyncio.to_thread(_shot)
            except Exception:  # noqa: BLE001
                return False

    async def fetch_hash_pages(self, url: str, count: int, wait_each: float = 3.0) -> list:
        """Load the SPA once, then walk URL hash #1..#count (allhomes sales
        history paginates via the hash), capturing each rendered DOM."""
        if self._page is None:
            return []

        def _walk() -> list:
            page = self._page
            page.get(url, timeout=self._config.request_timeout)
            page.wait(self._config.page_load_wait)  # initial load + bot check
            pages = [(1, page.html or "")]          # page 1 is already rendered
            for n in range(2, count + 1):
                # Click the page-number link (fires the SPA's click handler).
                # Setting location.hash alone does NOT load the next page.
                clicked = False
                selectors = (
                    f'xpath://a[normalize-space(text())="{n}" and contains(@href,"#{n}")]',
                    f'xpath://button[normalize-space(text())="{n}"]',
                    f'xpath://a[contains(@href,"#{n}") and normalize-space(text())="{n}"]',
                    f'xpath://a[normalize-space(text())="{n}"]',
                )
                for sel in selectors:
                    try:
                        el = page.ele(sel, timeout=2)
                    except Exception:  # noqa: BLE001
                        el = None
                    if el:
                        try:
                            el.click()
                            clicked = True
                            break
                        except Exception:  # noqa: BLE001
                            continue
                if not clicked:
                    break  # no further page link found -> reached the end
                page.wait(wait_each)                 # let the SPA load page n
                try:
                    page.scroll.to_bottom(); page.wait(0.3); page.scroll.to_top()
                except Exception:  # noqa: BLE001
                    pass
                pages.append((n, page.html or ""))
            return pages

        async with self._lock:
            try:
                raw = await asyncio.to_thread(_walk)
            except Exception as exc:  # noqa: BLE001
                return []
        return [
            FetchResult(url=f"{url}#{n}", final_url=f"{url}#{n}", status_code=200,
                        content_type="text/html", html=html)
            for n, html in raw
        ]

    async def browser_post_many(self, page_url: str, post_url: str, bodies: list,
                                wait_each: float = 0.6) -> list:
        """Load the page once (passes Kasada), then replay POSTs via the page's
        own patched fetch so bot-protection headers/cookies are attached."""
        if self._page is None:
            return []
        poll_js = (
            "window.__ghr=null;"
            "fetch(arguments[0],{method:'POST',"
            "headers:{'content-type':'application/json','accept':'application/json'},"
            "body:arguments[1],credentials:'include'})"
            ".then(function(r){return r.text();})"
            ".then(function(t){window.__ghr=t;})"
            ".catch(function(e){window.__ghr='ERR:'+e;});"
        )

        def _run() -> list:
            page = self._page
            page.get(page_url, timeout=self._config.request_timeout)
            page.wait(self._config.page_load_wait)  # establish session / clear bot check
            out = []
            for body in bodies:
                try:
                    page.run_js(poll_js, post_url, body)
                except Exception as exc:  # noqa: BLE001
                    out.append(f"ERR:run_js:{exc}"); continue
                text = None; waited = 0.0; budget = max(8.0, wait_each * 12)
                while waited < budget:
                    page.wait(0.25); waited += 0.25
                    try:
                        text = page.run_js("return window.__ghr;")
                    except Exception:  # noqa: BLE001
                        text = None
                    if text:
                        break
                out.append(text or "ERR:timeout")
                page.wait(wait_each)
            return out

        async with self._lock:
            try:
                return await asyncio.to_thread(_run)
            except Exception:  # noqa: BLE001
                return []

    async def close(self) -> None:
        await self._binary.close()
        if self._page is not None:
            page = self._page
            self._page = None
            try:
                await asyncio.to_thread(page.quit)
            except Exception:  # noqa: BLE001  # pragma: no cover
                pass
