"""Build every per-site report: pages, assets, crawl, broken, duplicate, external."""
from __future__ import annotations

import logging
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlsplit

from webarchiver.config.models import WebsiteConfig
from webarchiver.extract.models import AssetRecord, BrokenLink, PageRecord
from webarchiver.report.exporters import to_csv, to_excel, to_json
from webarchiver.report.models import SiteSummary
from webarchiver.report.sitemap import write_sitemap_json, write_sitemap_xml

_PAGE_COLS = [
    "URL", "Title", "Description", "Keywords", "Canonical URL",
    "Status Code", "Crawl Depth", "Language", "Content Hash", "Local File Path",
]
_ASSET_COLS = [
    "Asset URL", "Asset Type", "File Size", "Local Path",
    "Referenced By", "Content Hash", "Status",
]
_BROKEN_COLS = ["Source Page", "Broken Link", "Status Code", "Error Message"]
_DUP_COLS = ["Content Hash", "Type", "Count", "Items"]
_EXT_COLS = ["External Domain", "Count", "Referenced Pages"]


class ReportBuilder:
    """Produces the full report suite for a single site into ``workspace.root``."""

    def __init__(
        self,
        *,
        site: WebsiteConfig,
        root_dir: Path,
        logger: logging.Logger | None = None,
    ) -> None:
        self._site = site
        self._root = Path(root_dir)
        self._meta = self._root / "metadata"
        self._log = logger or logging.getLogger("webarchiver.report")

    # ---- row builders -------------------------------------------------
    @staticmethod
    def _page_rows(pages: list[PageRecord]) -> list[dict]:
        return [
            {
                "URL": p.url, "Title": p.title, "Description": p.meta_description,
                "Keywords": p.meta_keywords, "Canonical URL": p.canonical_url,
                "Status Code": p.status_code, "Crawl Depth": p.depth,
                "Language": p.language, "Content Hash": p.content_hash,
                "Local File Path": p.local_path,
            }
            for p in pages
        ]

    @staticmethod
    def _asset_rows(assets: list[AssetRecord]) -> list[dict]:
        return [
            {
                "Asset URL": a.url, "Asset Type": a.asset_type.value,
                "File Size": a.file_size, "Local Path": a.local_path,
                "Referenced By": "; ".join(sorted(a.referenced_by)),
                "Content Hash": a.content_hash,
                "Status": "ok" if a.downloaded else (a.error or "failed"),
            }
            for a in assets
        ]

    @staticmethod
    def _broken_rows(broken: list[BrokenLink]) -> list[dict]:
        return [
            {
                "Source Page": b.source_page, "Broken Link": b.broken_url,
                "Status Code": b.status_code, "Error Message": b.error_message,
            }
            for b in broken
        ]

    @staticmethod
    def _duplicate_rows(pages: list[PageRecord], assets: list[AssetRecord]) -> list[dict]:
        rows: list[dict] = []
        groups: dict[tuple[str, str], list[str]] = defaultdict(list)
        for p in pages:
            if p.content_hash:
                groups[(p.content_hash, "page")].append(p.url)
        for a in assets:
            if a.content_hash:
                groups[(a.content_hash, "asset")].append(a.url)
        for (digest, kind), items in groups.items():
            if len(items) > 1:
                rows.append({
                    "Content Hash": digest, "Type": kind,
                    "Count": len(items), "Items": "; ".join(sorted(items)),
                })
        return sorted(rows, key=lambda r: r["Count"], reverse=True)

    @staticmethod
    def _external_rows(pages: list[PageRecord]) -> list[dict]:
        domains: dict[str, dict] = {}
        for page in pages:
            for link in page.external_links:
                host = urlsplit(link).hostname or "(unknown)"
                entry = domains.setdefault(host, {"count": 0, "pages": set()})
                entry["count"] += 1
                entry["pages"].add(page.url)
        rows = [
            {
                "External Domain": host, "Count": data["count"],
                "Referenced Pages": "; ".join(sorted(data["pages"])),
            }
            for host, data in domains.items()
        ]
        return sorted(rows, key=lambda r: r["Count"], reverse=True)

    # ---- orchestration ------------------------------------------------
    def build_all(
        self,
        *,
        pages: list[PageRecord],
        assets: list[AssetRecord],
        broken: list[BrokenLink],
        summary: SiteSummary,
    ) -> None:
        page_rows = self._page_rows(pages)
        asset_rows = self._asset_rows(assets)
        broken_rows = self._broken_rows(broken)
        dup_rows = self._duplicate_rows(pages, assets)
        ext_rows = self._external_rows(pages)
        summary_rows = [summary.as_row()]

        # Sitemaps (always generated).
        write_sitemap_xml(pages, self._root / "sitemap.xml")
        write_sitemap_json(pages, self._root / "sitemap.json")

        if self._site.export_csv:
            to_csv(page_rows, self._root / "pages.csv", columns=_PAGE_COLS)
            to_csv(asset_rows, self._root / "assets.csv", columns=_ASSET_COLS)
            to_csv(summary_rows, self._root / "crawl_report.csv")
            to_csv(broken_rows, self._meta / "broken_links.csv", columns=_BROKEN_COLS)
            to_csv(dup_rows, self._meta / "duplicates.csv", columns=_DUP_COLS)
            to_csv(ext_rows, self._meta / "external_links.csv", columns=_EXT_COLS)

        if self._site.export_excel:
            to_excel({"pages": page_rows}, self._root / "pages.xlsx", columns={"pages": _PAGE_COLS})
            to_excel({"assets": asset_rows}, self._root / "assets.xlsx", columns={"assets": _ASSET_COLS})
            to_excel(
                {
                    "summary": summary_rows,
                    "broken_links": broken_rows,
                    "duplicates": dup_rows,
                    "external_links": ext_rows,
                },
                self._root / "crawl_report.xlsx",
                columns={
                    "broken_links": _BROKEN_COLS,
                    "duplicates": _DUP_COLS,
                    "external_links": _EXT_COLS,
                },
            )

        if self._site.export_json:
            to_json(
                {
                    "summary": summary.as_row(),
                    "pages": page_rows,
                    "assets": asset_rows,
                    "broken_links": broken_rows,
                    "duplicates": dup_rows,
                    "external_links": ext_rows,
                },
                self._meta / "inventory.json",
            )

        self._log.info(
            "Reports written: %d pages, %d assets, %d broken, %d duplicate groups, %d ext domains",
            len(page_rows), len(asset_rows), len(broken_rows), len(dup_rows), len(ext_rows),
        )
