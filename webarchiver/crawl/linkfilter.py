"""Heuristic ad / tracker / junk-link filtering and pagination detection.

This module guarantees a baseline of "correct links only" even when the
optional Ollama LLM filter is unavailable: ad-network domains, tracking
pixels, sponsored/banner paths and dangerous links (logout) are dropped
before they ever reach the crawl frontier or the asset downloader.
"""
from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlsplit

# Hostname fragments that identify ad networks / trackers / analytics.
_AD_HOST_TOKENS = (
    "doubleclick", "googlesyndication", "googleadservices", "googletagmanager",
    "googletagservices", "google-analytics", "adservice", "adnxs", "adsystem",
    "adsrvr", "adroll", "advertising", "taboola", "outbrain", "criteo",
    "rubiconproject", "pubmatic", "openx", "moatads", "scorecardresearch",
    "quantserve", "chartbeat", "hotjar", "mouseflow", "crazyegg", "clarity.ms",
    "branch.io", "amplitude", "mixpanel", "segment.io", "snapads", "smartadserver",
    "zedo", "revcontent", "mgid", "popads", "propellerads", "exoclick",
)

# Path segments that mark a URL as an ad / tracking / junk link.
_AD_PATH_SEGMENTS = {
    "ads", "ad", "adverts", "advert", "advertise", "advertising", "advertisement",
    "advertisements", "banners", "banner", "sponsored", "sponsor", "sponsors",
    "affiliate", "affiliates", "tracking", "tracker", "pixel", "beacon",
    "popunder", "popup", "doubleclick",
}

# Links that should never be crawled (state-changing or session endpoints).
_DANGEROUS_SEGMENTS = {"logout", "signout", "log-out", "sign-out", "delete", "remove"}

# Query parameters that identify pagination.
_PAGE_PARAMS = {"page", "p", "pg", "paged", "pagenum", "pagenumber", "start", "offset", "from"}

# Path patterns that identify pagination: /page/2, /page-2, /p2 …
_PAGE_PATH_RE = re.compile(r"/(?:page|pages|p)[/\-_]?(\d+)/?$", re.IGNORECASE)

# Anchor text that identifies a "next page" control.
_NEXT_TEXT = {
    "next", "next page", "next ›", "older", "older posts", "more",
    "more results", "load more", "›", "»", ">", ">>", "→",
}


def is_ad_url(url: str) -> bool:
    """Return True when ``url`` points at an ad network, tracker or junk path."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return True
    host = (parts.hostname or "").lower()
    if any(token in host for token in _AD_HOST_TOKENS):
        return True
    segments = {seg.lower() for seg in parts.path.split("/") if seg}
    if segments & _AD_PATH_SEGMENTS:
        return True
    return False


def is_dangerous_url(url: str) -> bool:
    """Return True for links that must never be crawled (logout etc.)."""
    segments = {seg.lower() for seg in urlsplit(url).path.split("/") if seg}
    return bool(segments & _DANGEROUS_SEGMENTS)


def is_crawlable(url: str) -> bool:
    """Combined gate used by the parser/crawler for internal links."""
    return not is_ad_url(url) and not is_dangerous_url(url)


def is_pagination_url(url: str) -> bool:
    """Return True when ``url`` looks like page 2..N of a paginated listing."""
    parts = urlsplit(url)
    if _PAGE_PATH_RE.search(parts.path or ""):
        return True
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        if key.lower() in _PAGE_PARAMS and value.isdigit():
            return True
    return False


def is_next_text(text: str) -> bool:
    """Return True when anchor ``text`` reads like a Next-page control."""
    return text.strip().lower() in _NEXT_TEXT
