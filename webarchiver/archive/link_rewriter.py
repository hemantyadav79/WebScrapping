"""Rewrite a saved page's links so the archive browses offline.

Given a map of page URLs -> local HTML paths and asset URLs -> local asset
paths (both relative to the site root), the rewriter converts every internal
hyperlink and asset reference inside a saved page into a relative path that
works from the ``file://`` location of that page.
"""
from __future__ import annotations

import posixpath

from bs4 import BeautifulSoup

from webarchiver.core.url_utils import normalize_url, resolve_url


class LinkRewriter:
    """Rewrites internal links and asset references for offline navigation."""

    def __init__(self, page_map: dict[str, str], asset_map: dict[str, str]) -> None:
        self._pages = page_map
        self._assets = asset_map

    def _relative(self, target_relpath: str, from_relpath: str) -> str:
        from_dir = posixpath.dirname(from_relpath)
        rel = posixpath.relpath(target_relpath, start=from_dir or ".")
        return rel

    def _lookup(self, base_url: str, raw: str) -> str | None:
        resolved = resolve_url(base_url, raw)
        if not resolved:
            return None
        norm = normalize_url(resolved)
        return self._pages.get(norm) or self._assets.get(norm)

    def rewrite(self, html: str, *, base_url: str, page_relpath: str) -> str:
        """Return ``html`` with internal references rewritten to local paths."""
        soup = BeautifulSoup(html or "", "lxml")

        attr_tags = [
            ("a", "href"), ("link", "href"), ("script", "src"),
            ("img", "src"), ("img", "data-src"), ("source", "src"),
            ("video", "src"), ("video", "poster"), ("audio", "src"),
            ("iframe", "src"),
        ]
        for tag_name, attr in attr_tags:
            for tag in soup.find_all(tag_name):
                value = tag.get(attr)
                if not value:
                    continue
                target = self._lookup(base_url, value)
                if target is not None:
                    tag[attr] = self._relative(target, page_relpath)

        # Rewrite srcset (comma-separated "url descriptor" pairs).
        for tag in soup.find_all(["img", "source"]):
            srcset = tag.get("srcset")
            if not srcset:
                continue
            new_parts = []
            for part in srcset.split(","):
                chunk = part.strip()
                if not chunk:
                    continue
                bits = chunk.split(" ", 1)
                target = self._lookup(base_url, bits[0])
                url_out = self._relative(target, page_relpath) if target else bits[0]
                new_parts.append(url_out + (f" {bits[1]}" if len(bits) > 1 else ""))
            tag["srcset"] = ", ".join(new_parts)

        return str(soup)
