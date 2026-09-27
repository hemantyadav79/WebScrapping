"""Domain record models produced during extraction and used by reporters."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class AssetType(str, Enum):
    IMAGE = "image"
    VIDEO = "video"
    DOCUMENT = "document"
    CSS = "css"
    JS = "js"
    FONT = "font"
    OTHER = "other"


@dataclass(slots=True)
class AssetRef:
    """A discovered asset reference before it is downloaded."""

    url: str
    asset_type: AssetType
    referenced_by: str  # page URL that referenced this asset


@dataclass(slots=True)
class AssetRecord:
    """A downloaded (or attempted) asset and its on-disk metadata."""

    url: str
    asset_type: AssetType
    local_path: str = ""
    file_size: int = 0
    content_hash: str = ""
    status_code: int = 0
    referenced_by: set[str] = field(default_factory=set)
    error: str | None = None

    @property
    def downloaded(self) -> bool:
        return self.error is None and bool(self.local_path)


@dataclass(slots=True)
class PageRecord:
    """Everything extracted from a single crawled page."""

    url: str
    final_url: str = ""
    status_code: int = 0
    depth: int = 0
    title: str = ""
    meta_description: str = ""
    meta_keywords: str = ""
    canonical_url: str = ""
    language: str = ""
    headings: dict[str, list[str]] = field(default_factory=dict)
    open_graph: dict[str, str] = field(default_factory=dict)
    structured_data: list[dict] = field(default_factory=list)
    internal_links: list[str] = field(default_factory=list)
    external_links: list[str] = field(default_factory=list)
    pagination_links: list[str] = field(default_factory=list)
    assets: list[AssetRef] = field(default_factory=list)
    local_path: str = ""
    content_hash: str = ""
    redirect_chain: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def downloaded(self) -> bool:
        return self.error is None and bool(self.local_path)


@dataclass(slots=True)
class BrokenLink:
    """A link that failed to resolve to a healthy page or asset."""

    source_page: str
    broken_url: str
    status_code: int
    error_message: str
