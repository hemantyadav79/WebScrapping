"""Persistent crawl state for resume/recovery (``crawl_state.json``).

The state captures enough to resume a crawl: the frontier (queue + visited),
all page records collected so far, downloaded asset records, and timing. It is
checkpointed periodically and on completion. Records use plain dicts so the
state file is human-inspectable.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from webarchiver.crawl.frontier import Frontier
from webarchiver.extract.models import AssetRecord, AssetType, PageRecord

STATE_VERSION = 1


def _page_to_dict(p: PageRecord) -> dict:
    return {
        "url": p.url, "final_url": p.final_url, "status_code": p.status_code,
        "depth": p.depth, "title": p.title, "meta_description": p.meta_description,
        "meta_keywords": p.meta_keywords, "canonical_url": p.canonical_url,
        "language": p.language, "open_graph": p.open_graph,
        "external_links": p.external_links, "local_path": p.local_path,
        "content_hash": p.content_hash, "redirect_chain": p.redirect_chain,
        "error": p.error,
    }


def _page_from_dict(d: dict) -> PageRecord:
    return PageRecord(
        url=d["url"], final_url=d.get("final_url", ""), status_code=d.get("status_code", 0),
        depth=d.get("depth", 0), title=d.get("title", ""),
        meta_description=d.get("meta_description", ""), meta_keywords=d.get("meta_keywords", ""),
        canonical_url=d.get("canonical_url", ""), language=d.get("language", ""),
        open_graph=d.get("open_graph", {}) or {}, external_links=d.get("external_links", []) or [],
        local_path=d.get("local_path", ""), content_hash=d.get("content_hash", ""),
        redirect_chain=d.get("redirect_chain", []) or [], error=d.get("error"),
    )


def _asset_to_dict(a: AssetRecord) -> dict:
    return {
        "url": a.url, "asset_type": a.asset_type.value, "local_path": a.local_path,
        "file_size": a.file_size, "content_hash": a.content_hash,
        "status_code": a.status_code, "referenced_by": sorted(a.referenced_by),
        "error": a.error,
    }


def _asset_from_dict(d: dict) -> AssetRecord:
    return AssetRecord(
        url=d["url"], asset_type=AssetType(d.get("asset_type", "other")),
        local_path=d.get("local_path", ""), file_size=d.get("file_size", 0),
        content_hash=d.get("content_hash", ""), status_code=d.get("status_code", 0),
        referenced_by=set(d.get("referenced_by", [])), error=d.get("error"),
    )


@dataclass(slots=True)
class CrawlState:
    """Serializable crawl checkpoint."""

    site_name: str
    site_url: str
    status: str = "in_progress"  # in_progress | crawled | completed
    started_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    frontier: Frontier = field(default_factory=Frontier)
    pages: list[PageRecord] = field(default_factory=list)
    assets: list[AssetRecord] = field(default_factory=list)
    extra: dict = field(default_factory=dict)  # pager progress: done page nums / detail urls

    def to_dict(self) -> dict:
        return {
            "version": STATE_VERSION,
            "site_name": self.site_name,
            "site_url": self.site_url,
            "status": self.status,
            "started_at": self.started_at,
            "updated_at": time.time(),
            "frontier": self.frontier.to_dict(),
            "pages": [_page_to_dict(p) for p in self.pages],
            "assets": [_asset_to_dict(a) for a in self.assets],
            "extra": self.extra,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "CrawlState":
        state = cls(site_name=data["site_name"], site_url=data["site_url"])
        state.status = data.get("status", "in_progress")
        state.started_at = data.get("started_at", time.time())
        state.updated_at = data.get("updated_at", time.time())
        state.frontier = Frontier.from_dict(data.get("frontier", {}))
        state.pages = [_page_from_dict(d) for d in data.get("pages", [])]
        state.assets = [_asset_from_dict(d) for d in data.get("assets", [])]
        state.extra = data.get("extra", {}) or {}
        return state

    def save(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(self.to_dict(), indent=2, default=str), encoding="utf-8")
        tmp.replace(path)  # atomic swap to avoid corrupt checkpoints

    @classmethod
    def load(cls, path: str | Path) -> "CrawlState | None":
        path = Path(path)
        if not path.is_file():
            return None
        try:
            return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, KeyError):
            return None
