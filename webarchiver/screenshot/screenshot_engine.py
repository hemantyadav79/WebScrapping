"""Full-page screenshot capture, delegated to the active render engine.

Only browser-backed engines (DrissionPage) can produce screenshots; the static
HTTP engine returns False and these pages are simply skipped. Capturing is
serialised because a single browser tab is shared.
"""
from __future__ import annotations

import logging

from webarchiver.archive.storage import Workspace
from webarchiver.extract.models import PageRecord
from webarchiver.fetch.engine import RenderEngine


class ScreenshotService:
    """Captures one screenshot per successfully crawled page."""

    def __init__(
        self,
        *,
        engine: RenderEngine,
        workspace: Workspace,
        logger: logging.Logger | None = None,
    ) -> None:
        self._engine = engine
        self._workspace = workspace
        self._log = logger or logging.getLogger("webarchiver.screenshot")

    async def capture_all(self, pages: list[PageRecord]) -> dict[str, str]:
        """Capture screenshots for healthy pages; return ``url -> relpath``."""
        captured: dict[str, str] = {}
        for page in pages:
            if page.error:
                continue
            dest = self._workspace.screenshot_path(page.final_url or page.url)
            ok = await self._engine.screenshot(page.final_url or page.url, str(dest))
            if ok:
                captured[page.url] = self._workspace.relpath(dest)
                self._log.debug("Screenshot captured: %s", page.url)
        if captured:
            self._log.info("Captured %d screenshots", len(captured))
        return captured
