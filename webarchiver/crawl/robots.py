"""Minimal robots.txt support (optional, config-driven)."""
from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

from webarchiver.fetch.engine import RenderEngine


class RobotsPolicy:
    """Wraps :class:`RobotFileParser`; allows everything if disabled or missing."""

    def __init__(self, user_agent: str, *, enabled: bool) -> None:
        self._ua = user_agent
        self._enabled = enabled
        self._parser: RobotFileParser | None = None

    async def load(self, root_url: str, engine: RenderEngine) -> None:
        if not self._enabled:
            return
        parts = urlsplit(root_url)
        robots_url = urlunsplit((parts.scheme, parts.netloc, "/robots.txt", "", ""))
        parser = RobotFileParser()
        result = await engine.fetch(robots_url)
        if result.ok and result.html:
            parser.parse(result.html.splitlines())
        else:
            parser.parse([])  # empty -> allow all
        self._parser = parser

    def can_fetch(self, url: str) -> bool:
        if not self._enabled or self._parser is None:
            return True
        return self._parser.can_fetch(self._ua, url)
