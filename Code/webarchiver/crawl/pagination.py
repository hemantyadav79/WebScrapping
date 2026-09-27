"""Deterministic pagination planning.

Many listing sites paginate with a predictable URL pattern (``list-1``,
``list-2`` … or ``page=1`` …). Relying on link discovery to follow these is
fragile: the same page also links to sibling views (``auction-times-1``,
``inspection-times-1``, ``map-1``) that match the allowed path but are NOT the
listing pages we want — so a BFS crawler wanders off.

Instead we detect the page-number token in the seed URL and generate the exact
ordered sequence of page URLs. The crawler then visits only those, in order.
"""
from __future__ import annotations

import re

# Each entry: (search pattern, function building page N's URL from the seed).
_PATH_LIST = re.compile(r"/list-(\d+)\b")
_PATH_AUCTION = re.compile(r"/auction-times-(\d+)\b")
_PATH_PAGE = re.compile(r"/page-(\d+)\b")
_QUERY_PAGE = re.compile(r"([?&]page=)(\d+)\b")


def plan_param_pages(seed_url: str, max_pages: int, param: str) -> list[str]:
    """Generate ordered pages by setting ``?<param>=N`` on the seed URL.

    Used by sites (e.g. allhomes /research) that paginate a section via a query
    parameter rather than a path token. Page numbering starts at the param's
    current value if present, else 1.
    """
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
    if not seed_url or max_pages < 1 or not param:
        return []
    parts = urlsplit(seed_url)
    base_q = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != param]
    existing = dict(parse_qsl(parts.query))
    try:
        start = int(existing.get(param, "1"))
    except ValueError:
        start = 1
    out = []
    for n in range(start, start + max_pages):
        q = urlencode(base_q + [(param, str(n))])
        out.append(urlunsplit((parts.scheme, parts.netloc, parts.path, q, "")))
    return out


def plan_pages(seed_url: str, max_pages: int) -> list[str]:
    """Return ordered page URLs derived from ``seed_url``.

    Returns an empty list if no known pagination pattern is found (the caller
    then falls back to normal link-following).
    """
    if not seed_url or max_pages < 1:
        return []

    m = _PATH_AUCTION.search(seed_url)
    if m:
        start = int(m.group(1))
        return [_PATH_AUCTION.sub(f"/auction-times-{n}", seed_url, count=1)
                for n in range(start, start + max_pages)]

    m = _PATH_LIST.search(seed_url)
    if m:
        start = int(m.group(1))
        return [_PATH_LIST.sub(f"/list-{n}", seed_url, count=1)
                for n in range(start, start + max_pages)]

    m = _PATH_PAGE.search(seed_url)
    if m:
        start = int(m.group(1))
        return [_PATH_PAGE.sub(f"/page-{n}", seed_url, count=1)
                for n in range(start, start + max_pages)]

    m = _QUERY_PAGE.search(seed_url)
    if m:
        start = int(m.group(2))
        return [_QUERY_PAGE.sub(lambda mm, n=n: f"{mm.group(1)}{n}", seed_url, count=1)
                for n in range(start, start + max_pages)]

    return []
