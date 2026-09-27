"""HTML content & metadata extraction using BeautifulSoup + lxml."""
from __future__ import annotations

import json

from bs4 import BeautifulSoup

from webarchiver.core.url_utils import is_same_domain, normalize_url, registrable_host, resolve_url
from webarchiver.crawl.linkfilter import (
    is_ad_url,
    is_crawlable,
    is_next_text,
    is_pagination_url,
)
from webarchiver.extract.classifier import classify
from webarchiver.extract.models import AssetRef, AssetType, PageRecord


class PageParser:
    """Parse rendered HTML into a :class:`PageRecord`."""

    def __init__(self, *, root_host: str, include_subdomains: bool = False) -> None:
        self._root_host = root_host
        self._include_subdomains = include_subdomains

    def parse(self, html: str, base_url: str) -> PageRecord:
        soup = BeautifulSoup(html or "", "lxml")
        record = PageRecord(url=base_url, final_url=base_url)

        record.title = self._text(soup.title)
        record.language = (soup.html.get("lang", "").strip() if soup.html else "")
        record.meta_description = self._meta(soup, "name", "description")
        record.meta_keywords = self._meta(soup, "name", "keywords")
        record.canonical_url = self._canonical(soup, base_url)
        record.headings = self._headings(soup)
        record.open_graph = self._open_graph(soup)
        record.structured_data = self._structured_data(soup)

        self._links_and_assets(soup, base_url, record)
        return record

    # ---- field extractors --------------------------------------------
    @staticmethod
    def _text(node: object) -> str:
        return node.get_text(strip=True) if node is not None else ""

    @staticmethod
    def _meta(soup: BeautifulSoup, attr: str, value: str) -> str:
        tag = soup.find("meta", attrs={attr: value})
        return tag.get("content", "").strip() if tag else ""

    def _canonical(self, soup: BeautifulSoup, base_url: str) -> str:
        tag = soup.find("link", rel=lambda v: v and "canonical" in v)
        if tag and tag.get("href"):
            resolved = resolve_url(base_url, tag["href"])
            return resolved or ""
        return ""

    @staticmethod
    def _headings(soup: BeautifulSoup) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for level in range(1, 7):
            tag = f"h{level}"
            texts = [h.get_text(strip=True) for h in soup.find_all(tag) if h.get_text(strip=True)]
            if texts:
                out[tag] = texts
        return out

    @staticmethod
    def _open_graph(soup: BeautifulSoup) -> dict[str, str]:
        og: dict[str, str] = {}
        for tag in soup.find_all("meta"):
            prop = tag.get("property", "")
            if prop.startswith(("og:", "twitter:")) and tag.get("content"):
                og[prop] = tag["content"].strip()
        return og

    @staticmethod
    def _structured_data(soup: BeautifulSoup) -> list[dict]:
        data: list[dict] = []
        for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
            raw = script.string or script.get_text()
            if not raw:
                continue
            try:
                parsed = json.loads(raw)
            except (json.JSONDecodeError, ValueError):
                continue
            if isinstance(parsed, list):
                data.extend(p for p in parsed if isinstance(p, dict))
            elif isinstance(parsed, dict):
                data.append(parsed)
        return data

    # ---- link & asset discovery --------------------------------------
    def _is_internal(self, url: str) -> bool:
        return is_same_domain(url, self._root_host, include_subdomains=self._include_subdomains)

    def _links_and_assets(self, soup: BeautifulSoup, base_url: str, record: PageRecord) -> None:
        internal: set[str] = set()
        external: set[str] = set()
        pagination: set[str] = set()
        assets: dict[str, AssetRef] = {}

        def add_asset(raw: str | None, hint: AssetType | None) -> None:
            resolved = resolve_url(base_url, raw or "")
            if not resolved:
                return
            norm = normalize_url(resolved)
            if is_ad_url(norm):  # never download tracking pixels / ad creatives
                return
            atype = classify(norm, hint=hint)
            if atype is AssetType.OTHER and hint is None:
                return
            assets.setdefault(norm, AssetRef(url=norm, asset_type=atype, referenced_by=record.url))

        def _anchor_is_next(a) -> bool:
            rel = " ".join(a.get("rel", []) or []).lower()
            if "next" in rel:
                return True
            label = (a.get("aria-label", "") or "").strip().lower()
            if "next" in label:
                return True
            return is_next_text(a.get_text(" ", strip=True))

        # Anchor links -> navigation pages and/or downloadable documents/media.
        for a in soup.find_all("a", href=True):
            resolved = resolve_url(base_url, a["href"])
            if not resolved:
                continue
            norm = normalize_url(resolved)
            atype = classify(norm)
            is_downloadable = atype in (AssetType.DOCUMENT, AssetType.IMAGE, AssetType.VIDEO)
            if is_downloadable:
                # Treat as an asset to download, never as a page to crawl.
                add_asset(a["href"], atype)
                if not self._is_internal(norm):
                    external.add(norm)
                continue
            if self._is_internal(norm):
                if not is_crawlable(norm):  # drop ad/tracking/logout links
                    continue
                internal.add(norm)
                if is_pagination_url(norm) or _anchor_is_next(a):
                    pagination.add(norm)
            else:
                external.add(norm)

        # <link rel="next"> pagination hint (common on listing/blog pages).
        for link in soup.find_all("link", href=True):
            rel = " ".join(link.get("rel", []) or []).lower()
            if "next" in rel:
                resolved = resolve_url(base_url, link["href"])
                if resolved:
                    norm = normalize_url(resolved)
                    if self._is_internal(norm) and is_crawlable(norm):
                        internal.add(norm)
                        pagination.add(norm)

        # Images (src + lazy + srcset).
        for img in soup.find_all("img"):
            for attr in ("src", "data-src", "data-original"):
                add_asset(img.get(attr), AssetType.IMAGE)
            for candidate in (img.get("srcset", "") or "").split(","):
                add_asset(candidate.strip().split(" ")[0], AssetType.IMAGE)

        # <picture><source> and <video>/<audio><source>.
        for source in soup.find_all("source"):
            add_asset(source.get("src"), None)
            for candidate in (source.get("srcset", "") or "").split(","):
                add_asset(candidate.strip().split(" ")[0], AssetType.IMAGE)
        for video in soup.find_all("video"):
            add_asset(video.get("src"), AssetType.VIDEO)
            add_asset(video.get("poster"), AssetType.IMAGE)

        # Stylesheets, scripts, icons, fonts.
        for link in soup.find_all("link", href=True):
            rel = " ".join(link.get("rel", [])).lower()
            if "stylesheet" in rel:
                add_asset(link["href"], AssetType.CSS)
            elif "icon" in rel:
                add_asset(link["href"], AssetType.IMAGE)
            elif link.get("as") == "font" or "preload" in rel:
                add_asset(link["href"], AssetType.FONT)
        for script in soup.find_all("script", src=True):
            add_asset(script["src"], AssetType.JS)

        record.internal_links = sorted(internal)
        record.external_links = sorted(external)
        record.pagination_links = sorted(pagination)
        record.assets = list(assets.values())

    @property
    def root_host(self) -> str:
        return self._root_host


def root_host_of(url: str) -> str:
    """Convenience wrapper returning the registrable host of ``url``."""
    return registrable_host(url)
