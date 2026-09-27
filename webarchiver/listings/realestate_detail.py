"""Realestate.com.au single-property detail extraction (DOM-based).

The realestate listing page renders its key extras directly into the HTML
(the Argonaut/urql store on the detail page only holds showcase items), so we
read them from stable ``data-testid`` markers and text patterns:

  * Property ID            -> text "Property ID: <n>"
  * Page views             -> text "<n> page views"
  * Description            -> data-testid="PropertyDescription"
  * Property features      -> data-testid="top-property-features-section"
  * Property highlights    -> heading "Property highlights" (best effort)

This module is additive and only runs when ``fetch_details`` is enabled for a
realestate site. It does not touch the search-results extraction logic.
"""
from __future__ import annotations

import re
from typing import Any

from bs4 import BeautifulSoup


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def _features(soup: BeautifulSoup) -> list[str]:
    out: list[str] = []
    for tid in ("top-property-features-section", "listing-details__property-features"):
        sec = soup.find(attrs={"data-testid": tid})
        if not sec:
            continue
        nodes = sec.find_all("li") or sec.find_all("div")
        for n in nodes:
            t = _clean(n.get_text(" ", strip=True))
            if t and len(t) < 80 and t not in out:
                out.append(t)
        if out:
            break
    return out


def _highlights(soup: BeautifulSoup) -> list[str]:
    node = soup.find(["h2", "h3"], string=re.compile(r"Property highlights", re.I))
    if not node:
        return []
    cont = node.find_next(["ul", "div"])
    if not cont:
        return []
    out: list[str] = []
    for n in cont.find_all(["li", "div"]):
        t = _clean(n.get_text(" ", strip=True))
        if t and len(t) < 80 and t not in out:
            out.append(t)
    return out[:20]


def parse_realestate_detail(html: str, url: str = "") -> dict | None:
    """Return realestate detail extras for one property page, or None."""
    if not html:
        return None
    soup = BeautifulSoup(html, "lxml")

    pid = re.search(r"Property ID:\s*([0-9]+)", html)
    views = re.search(r"([\d,]+)\s+page views", html)
    desc_node = soup.find(attrs={"data-testid": "PropertyDescription"})

    detail: dict[str, Any] = {
        "url": url,
        "property_id": pid.group(1) if pid else "",
        "page_views": int(views.group(1).replace(",", "")) if views else None,
        "description": _clean(desc_node.get_text(" ", strip=True)) if desc_node else "",
        "property_features": _features(soup),
        "property_highlights": _highlights(soup),
    }
    return detail
