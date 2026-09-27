"""Allhomes sold-history pagination via its GraphQL persisted query.

Allhomes paginates sales history client-side and caches early pages, so neither
URL changes nor DOM clicks reliably reach every page. But each page is backed by
a GraphQL call (operation ``updateHistoryForLocality``) using an Apollo
*persisted query* (only a hash + variables are sent). Replaying that POST with
``page = 1..N`` returns every page directly — fast and complete.

The POST is executed inside the real browser (see DrissionRenderEngine) so the
Kasada bot-protection headers/cookies are applied automatically.
"""
from __future__ import annotations

import json
import re
from urllib.parse import urlsplit

# Captured from the live site (DevTools → Network → graphql payload).
DEFAULT_HASH = "d16064a1e14de8b8192be6bece8e2bb0dec81e1d46d0736461fd8c9484211996"
OPERATION = "updateHistoryForLocality"


def slug_from_url(url: str) -> str:
    """Return the locality slug from a /research/<slug> URL."""
    m = re.search(r"/research/([^/?#]+)", url or "")
    return m.group(1) if m else ""


def graphql_endpoint(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme or 'https'}://{parts.netloc}/graphql"


def build_body(slug: str, page: int, page_size: int = 10, sha256_hash: str = DEFAULT_HASH) -> str:
    """Build the JSON POST body for one sales-history page."""
    return json.dumps({
        "operationName": OPERATION,
        "variables": {
            "locality": {"slug": slug, "type": "DIVISION"},
            "filters": {"beds": {"lower": 0}, "baths": {"lower": 0}, "parks": {"lower": 0}},
            "duration": {"unit": "ALL"},
            "sort": {"type": "SOLD_AGE", "order": "DESC"},
            "page": page,
            "pageSize": page_size,
        },
        "extensions": {"persistedQuery": {"version": 1, "sha256Hash": sha256_hash}},
    })


def parse_response(text: str) -> tuple[list[dict], int | None, str | None]:
    """Parse a GraphQL response into (nodes, totalPages, error)."""
    if not text or text.startswith("ERR:"):
        return [], None, (text or "empty response")
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return [], None, "invalid json"
    if isinstance(data, dict) and data.get("errors"):
        msg = (data["errors"][0] or {}).get("message", "graphql error")
        if "PersistedQuery" in msg:
            return [], None, "PersistedQueryNotFound"
        return [], None, msg
    nodes, total = _find_history(data)
    return nodes, total, None


def _find_history(obj) -> tuple[list[dict], int | None]:
    """Deep-search the response for the history block ({nodes, totalPages})."""
    stack = [obj]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            if isinstance(node.get("nodes"), list) and ("totalPages" in node or "total" in node):
                return node["nodes"], node.get("totalPages")
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    return [], None
