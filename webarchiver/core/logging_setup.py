"""Enterprise-grade logging configuration.

Provides per-website log files (crawl / download / error) plus a console
stream. Loggers are namespaced as ``webarchiver.<site>.<channel>`` so output is
cleanly separated on disk.
"""
from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"
_configured_root = False


def configure_root(level: str = "INFO") -> None:
    """Configure the console handler on the root application logger once."""
    global _configured_root
    root = logging.getLogger("webarchiver")
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    if not _configured_root:
        console = logging.StreamHandler()
        console.setFormatter(logging.Formatter(_FORMAT, _DATEFMT))
        root.addHandler(console)
        root.propagate = False
        _configured_root = True


def _file_handler(path: Path, level: int) -> logging.Handler:
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        path, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(_FORMAT, _DATEFMT))
    return handler


class SiteLoggers:
    """Bundle of channel loggers for a single website, writing to ``logs/``."""

    def __init__(self, site_name: str, logs_dir: Path, level: str = "INFO") -> None:
        self.site_name = site_name
        self.logs_dir = Path(logs_dir)
        self._level = getattr(logging, level.upper(), logging.INFO)
        self._handlers: list[logging.Handler] = []

        self.crawl = self._make("crawl", "crawl.log")
        self.download = self._make("download", "download.log")
        self.error = self._make("error", "error.log", min_level=logging.WARNING)

    def _make(self, channel: str, filename: str, min_level: int | None = None) -> logging.Logger:
        logger = logging.getLogger(f"webarchiver.{self.site_name}.{channel}")
        logger.setLevel(self._level)
        logger.propagate = True  # bubble up to console handler on the root logger
        handler = _file_handler(self.logs_dir / filename, min_level or self._level)
        logger.addHandler(handler)
        self._handlers.append(handler)
        return logger

    def close(self) -> None:
        """Flush and detach all file handlers (important for long runs / tests)."""
        for logger_name in ("crawl", "download", "error"):
            logger = getattr(self, logger_name)
            for handler in list(logger.handlers):
                handler.flush()
                handler.close()
                logger.removeHandler(handler)
        self._handlers.clear()

    def __enter__(self) -> SiteLoggers:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
