"""Summary models used by reporting and the master report."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class SiteSummary:
    """High-level metrics for one website crawl (the crawl validation report)."""

    name: str
    url: str
    pages_discovered: int = 0
    pages_downloaded: int = 0
    pages_failed: int = 0
    blocked_pages: int = 0
    images_downloaded: int = 0
    videos_downloaded: int = 0
    documents_downloaded: int = 0
    css_downloaded: int = 0
    js_downloaded: int = 0
    fonts_downloaded: int = 0
    assets_failed: int = 0
    duplicate_assets: int = 0
    broken_links: int = 0
    external_domains: int = 0
    screenshots: int = 0
    listings_extracted: int = 0
    listings_rejected: int = 0
    avg_quality_score: float = 0.0
    total_size_bytes: int = 0
    crawl_duration_sec: float = 0.0
    output_dir: str = ""
    errors: list[str] = field(default_factory=list)

    @property
    def total_size_human(self) -> str:
        size = float(self.total_size_bytes)
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if size < 1024 or unit == "TB":
                return f"{size:.2f} {unit}"
            size /= 1024
        return f"{size:.2f} TB"

    def as_row(self) -> dict[str, object]:
        return {
            "Website": self.name,
            "URL": self.url,
            "Pages Discovered": self.pages_discovered,
            "Pages Downloaded": self.pages_downloaded,
            "Pages Failed": self.pages_failed,
            "Blocked Pages": self.blocked_pages,
            "Images Downloaded": self.images_downloaded,
            "Videos Downloaded": self.videos_downloaded,
            "Documents Downloaded": self.documents_downloaded,
            "CSS Downloaded": self.css_downloaded,
            "JS Downloaded": self.js_downloaded,
            "Fonts Downloaded": self.fonts_downloaded,
            "Assets Failed": self.assets_failed,
            "Duplicate Assets": self.duplicate_assets,
            "Broken Links": self.broken_links,
            "External Domains": self.external_domains,
            "Screenshots": self.screenshots,
            "Listings Extracted": self.listings_extracted,
            "Listings Rejected": self.listings_rejected,
            "Avg Quality Score": self.avg_quality_score,
            "Total Size": self.total_size_human,
            "Total Size (bytes)": self.total_size_bytes,
            "Crawl Duration (s)": round(self.crawl_duration_sec, 2),
            "Output Dir": self.output_dir,
        }
