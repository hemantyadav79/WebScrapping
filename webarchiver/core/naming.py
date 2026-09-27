"""Safe, deterministic file and path naming.

Web URLs map to arbitrary, potentially hostile path strings. This module turns
a URL into a safe local file path that round-trips deterministically and avoids
path traversal, reserved names and over-long components.
"""
from __future__ import annotations

import hashlib
import re
from urllib.parse import urlsplit, unquote

_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WINDOWS_RESERVED = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}
_MAX_COMPONENT = 150


def safe_component(name: str, *, default: str = "_") -> str:
    """Sanitise a single path component (no separators)."""
    name = unquote(name)
    name = _UNSAFE.sub("_", name).strip(" .")
    if not name:
        return default
    if name.lower() in _WINDOWS_RESERVED:
        name = f"_{name}"
    if len(name) > _MAX_COMPONENT:
        digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:10]
        name = f"{name[:_MAX_COMPONENT - 11]}_{digest}"
    return name


def url_to_html_filename(url: str) -> str:
    """Map a page URL to a stable ``.html`` filename.

    Index/empty paths become ``index.html``; query strings are folded into the
    name via a short hash so distinct query variants do not collide.
    """
    parts = urlsplit(url)
    path = parts.path
    if not path or path.endswith("/"):
        path = path + "index"
    segments = [safe_component(s) for s in path.split("/") if s]
    base = "_".join(segments) if segments else "index"

    if base.lower().endswith((".html", ".htm")):
        base = base.rsplit(".", 1)[0]

    if parts.query:
        qhash = hashlib.sha1(parts.query.encode("utf-8")).hexdigest()[:8]
        base = f"{base}__{qhash}"

    return f"{safe_component(base)}.html"


def asset_filename(url: str, *, fallback_ext: str = "") -> str:
    """Derive a safe filename for an asset URL, preserving its extension."""
    parts = urlsplit(url)
    last = parts.path.rsplit("/", 1)[-1] if parts.path else ""
    last = unquote(last)
    name, dot, ext = last.rpartition(".")
    if not dot:
        name, ext = last, fallback_ext.lstrip(".")

    name = safe_component(name or "asset")
    ext = safe_component(ext) if ext else ""

    if parts.query:
        qhash = hashlib.sha1(parts.query.encode("utf-8")).hexdigest()[:8]
        name = f"{name}__{qhash}"

    return f"{name}.{ext}" if ext else name
