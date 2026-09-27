"""Workspace: builds the required folder tree and allocates local file paths.

A single :class:`Workspace` owns one website's backup directory. It guarantees
the spec's folder layout exists and hands out collision-free, safe paths for
pages, assets and screenshots. All path allocation is centralised here so the
rest of the system never constructs filesystem paths by hand.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from webarchiver.core.naming import asset_filename, safe_component, url_to_html_filename
from webarchiver.extract.classifier import subfolder_for
from webarchiver.extract.models import AssetType

# Directories created for every website backup.
_DIRS = [
    "html",
    "images/jpg", "images/png", "images/webp", "images/svg", "images/gif", "images/other",
    "videos", "pdf", "documents", "css", "js", "fonts",
    "screenshots", "logs", "metadata",
]

# Listings-only mode keeps just what it fills: photos, reports, logs.
_LISTINGS_DIRS = [
    "images/jpg", "images/png", "images/webp", "images/svg", "images/gif",
    "images/other", "logs", "metadata",
]


class Workspace:
    """Owns the on-disk layout for a single website backup."""

    def __init__(self, output_root: str | Path, site_name: str) -> None:
        self.root = Path(output_root) / safe_component(site_name)
        self._used: dict[str, set[str]] = {}

    # ---- construction -------------------------------------------------
    def create_tree(self, *, listings_only: bool = False) -> None:
        dirs = _LISTINGS_DIRS if listings_only else _DIRS
        for rel in dirs:
            (self.root / rel).mkdir(parents=True, exist_ok=True)

    def dir(self, rel: str) -> Path:
        path = self.root / rel
        path.mkdir(parents=True, exist_ok=True)
        return path

    # ---- path allocation ----------------------------------------------
    def _allocate(self, folder: Path, filename: str) -> Path:
        """Return a unique path inside ``folder`` for ``filename``."""
        key = str(folder)
        used = self._used.setdefault(key, set())
        stem, dot, ext = filename.rpartition(".")
        if not dot:
            stem, ext = filename, ""
        candidate = filename
        n = 1
        while candidate in used:
            candidate = f"{stem}_{n}.{ext}" if ext else f"{stem}_{n}"
            n += 1
        used.add(candidate)
        return folder / candidate

    def html_path(self, url: str) -> Path:
        return self._allocate(self.root / "html", url_to_html_filename(url))

    def asset_path(self, url: str, asset_type: AssetType) -> Path:
        folder = self.root / subfolder_for(asset_type, url)
        folder.mkdir(parents=True, exist_ok=True)
        return self._allocate(folder, asset_filename(url))

    def screenshot_path(self, url: str) -> Path:
        name = url_to_html_filename(url).rsplit(".", 1)[0] + ".png"
        return self._allocate(self.root / "screenshots", name)

    # ---- writers ------------------------------------------------------
    def write_text(self, path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", errors="replace")

    def write_bytes(self, path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def relpath(self, path: str | Path) -> str:
        """Return ``path`` relative to the site root, using POSIX separators."""
        try:
            return Path(path).resolve().relative_to(self.root.resolve()).as_posix()
        except ValueError:
            return str(path)

    @staticmethod
    def short_hash(text: str) -> str:
        return hashlib.sha1(text.encode("utf-8")).hexdigest()[:8]
