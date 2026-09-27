"""Classify asset URLs into types and target subfolders."""
from __future__ import annotations

from urllib.parse import urlsplit

from webarchiver.extract.models import AssetType

_IMAGE_EXT = {"jpg", "jpeg", "png", "gif", "webp", "svg", "bmp", "ico", "tiff"}
_VIDEO_EXT = {"mp4", "webm", "ogg", "mov", "avi", "mkv", "m4v"}
_DOC_EXT = {"pdf", "doc", "docx", "ppt", "pptx", "xls", "xlsx", "txt", "csv", "rtf", "odt"}
_FONT_EXT = {"woff", "woff2", "ttf", "otf", "eot"}
_CSS_EXT = {"css"}
_JS_EXT = {"js", "mjs"}

# Image subfolders required by the spec (others fall back to "other").
_IMAGE_SUBFOLDERS = {"jpg", "jpeg", "png", "webp", "svg", "gif"}


def extension_of(url: str) -> str:
    """Return the lowercased file extension of ``url`` (no dot)."""
    path = urlsplit(url).path
    last = path.rsplit("/", 1)[-1]
    if "." not in last:
        return ""
    return last.rsplit(".", 1)[-1].lower()


def classify(url: str, *, hint: AssetType | None = None) -> AssetType:
    """Classify ``url`` by extension, with an optional contextual ``hint``."""
    ext = extension_of(url)
    if ext in _IMAGE_EXT:
        return AssetType.IMAGE
    if ext in _VIDEO_EXT:
        return AssetType.VIDEO
    if ext in _DOC_EXT:
        return AssetType.DOCUMENT
    if ext in _FONT_EXT:
        return AssetType.FONT
    if ext in _CSS_EXT:
        return AssetType.CSS
    if ext in _JS_EXT:
        return AssetType.JS
    return hint or AssetType.OTHER


def subfolder_for(asset_type: AssetType, url: str) -> str:
    """Return the relative storage subfolder for an asset.

    Images are split by format (jpg/png/webp/svg/...) per the required layout;
    a normalised ``jpeg`` collapses into ``jpg``.
    """
    if asset_type is AssetType.IMAGE:
        ext = extension_of(url)
        if ext == "jpeg":
            ext = "jpg"
        return f"images/{ext if ext in _IMAGE_SUBFOLDERS else 'other'}"
    if asset_type is AssetType.DOCUMENT:
        return "pdf" if extension_of(url) == "pdf" else "documents"
    return {
        AssetType.VIDEO: "videos",
        AssetType.CSS: "css",
        AssetType.JS: "js",
        AssetType.FONT: "fonts",
        AssetType.OTHER: "other",
    }[asset_type]
