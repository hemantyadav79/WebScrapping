"""Next.js ``__NEXT_DATA__`` hydration-JSON extraction.

Modern portals (realestate.com.au) embed fully structured listing data in a
``<script id="__NEXT_DATA__">`` JSON blob. Parsing that is far more robust than
scraping CSS classes, which change often. This module locates the blob, deep-
walks it for listing-shaped objects, and maps them with tolerant field getters
so minor schema changes don't break extraction. The caller falls back to DOM
parsing when no hydration data is present.
"""
from __future__ import annotations

import json
from typing import Any, Iterator

from bs4 import BeautifulSoup

_HYDRATION_IDS = ("__NEXT_DATA__", "__NUXT_DATA__", "__PRELOADED_STATE__")


def parse_hydration(html: str) -> Any | None:
    """Return the parsed hydration JSON object, or None if absent/invalid."""
    soup = BeautifulSoup(html or "", "lxml")
    tag = None
    for hid in _HYDRATION_IDS:
        tag = soup.find("script", id=hid)
        if tag:
            break
    if tag is None:
        tag = soup.find("script", attrs={"type": "application/json", "id": True})
    if tag is None:
        return None
    raw = tag.string or tag.get_text()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None


def _looks_like_listing(node: dict) -> bool:
    keys = node.keys()
    has_addr = "address" in keys or "addressParts" in keys
    has_detail = any(k in keys for k in ("price", "generalFeatures", "propertyType", "bedrooms"))
    return has_addr and has_detail


def iter_listings(data: Any) -> Iterator[dict]:
    """Depth-first walk yielding every listing-shaped dict in ``data``."""
    stack: list[Any] = [data]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            if _looks_like_listing(node):
                yield node
                continue
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)


def iter_property_objects(data: Any, detail_keys: tuple[str, ...]) -> Iterator[dict]:
    """Yield dicts that have an address-like key AND any of ``detail_keys``
    (e.g. price/soldPrice/contractDate) — generalises iter_listings to sold
    history and other portals whose schema differs."""
    stack: list[Any] = [data]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            keys_l = {str(k).lower() for k in node.keys()}
            has_addr = any("address" in k for k in keys_l)
            has_detail = any(any(d in k for k in keys_l) for d in detail_keys)
            if has_addr and has_detail:
                yield node
                continue
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)


def first(obj: Any, paths: list[list[str]], default: str = "") -> str:
    """Return the first present value among dotted ``paths`` as a string."""
    for path in paths:
        cur: Any = obj
        ok = True
        for key in path:
            if isinstance(cur, dict) and key in cur:
                cur = cur[key]
            elif isinstance(cur, list) and key.isdigit() and int(key) < len(cur):
                cur = cur[int(key)]
            else:
                ok = False
                break
        if ok and cur not in (None, "", [], {}):
            if isinstance(cur, (str, int, float)):
                return str(cur)
            if isinstance(cur, dict):
                # common scalar holders
                for k in ("value", "display", "text", "fullAddress"):
                    if k in cur and isinstance(cur[k], (str, int, float)):
                        return str(cur[k])
    return default


def deep_scalar(obj: Any, name_parts: tuple[str, ...], *, max_depth: int = 6) -> str:
    """Recursively find the first scalar under a key whose name contains any of
    ``name_parts`` (case-insensitive). Tolerates {value|display|text} holders.
    Robust to schema changes where exact paths are unknown.
    """
    def walk(node: Any, depth: int) -> str:
        if depth > max_depth:
            return ""
        if isinstance(node, dict):
            for key, val in node.items():
                kl = str(key).lower()
                if any(p in kl for p in name_parts):
                    if isinstance(val, (str, int, float)):
                        return str(val)
                    if isinstance(val, dict):
                        for hk in ("value", "display", "text", "fullAddress"):
                            hv = val.get(hk)
                            if isinstance(hv, (str, int, float)):
                                return str(hv)
                            if isinstance(hv, dict):
                                for hk2 in ("value", "fullAddress", "display"):
                                    if isinstance(hv.get(hk2), (str, int, float)):
                                        return str(hv[hk2])
            for val in node.values():
                got = walk(val, depth + 1)
                if got:
                    return got
        elif isinstance(node, list):
            for item in node:
                got = walk(item, depth + 1)
                if got:
                    return got
        return ""
    return walk(obj, 0)
