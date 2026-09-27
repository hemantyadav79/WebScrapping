"""Allhomes single-property detail extraction.

A property page embeds the entire record in a script as
``window['__domain_group/APP_PROPS'] = { ... }`` under ``body.property``.
We extract that object and map every section the user wants:
images, description, key details, block information (+ unimproved-value
history), nearby sales / sold comparables, risks & heritage, what's nearby,
suburb information, the property's own sale history, plus listing id,
page views, last-updated, inspections, auctions, and agents (with photos).
"""
from __future__ import annotations

import json
import re
from typing import Any

from bs4 import BeautifulSoup

_MARK = "__domain_group/APP_PROPS"


def _strip_typename(o: Any) -> Any:
    if isinstance(o, dict):
        return {k: _strip_typename(v) for k, v in o.items() if k != "__typename"}
    if isinstance(o, list):
        return [_strip_typename(x) for x in o]
    return o


def _extract_app_props(html: str) -> dict | None:
    soup = BeautifulSoup(html or "", "lxml")
    blob = ""
    for sc in soup.find_all("script"):
        t = sc.string or sc.get_text() or ""
        if _MARK in t:
            blob = t
            break
    if not blob:
        return None
    i = blob.find("=", blob.find(_MARK + "']"))
    if i < 0:
        return None
    s = blob[i + 1:].lstrip()
    depth = 0; instr = False; esc = False; end = 0
    for k, ch in enumerate(s):
        if esc: esc = False; continue
        if ch == "\\": esc = True; continue
        if ch == '"': instr = not instr
        if instr: continue
        if ch == "{": depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = k + 1; break
    if not end:
        return None
    try:
        return json.loads(s[:end])
    except (json.JSONDecodeError, ValueError):
        return None


def _img_items(media: dict) -> list[str]:
    out = []
    for it in (media or {}).get("items", []) or []:
        src = it.get("imageSrc") or it.get("url") or ""
        if isinstance(src, str) and src:
            from webarchiver.listings.realestate_parse import _normalize_allhomes_image
            src = _normalize_allhomes_image(src)
            out.append(re.sub(r"_(l|pl|m|t)\.jpg$", "_hd.jpg", src))  # prefer hi-res
    # de-dup preserving order
    seen = set(); uniq = []
    for u in out:
        if u not in seen:
            seen.add(u); uniq.append(u)
    return uniq


def parse_allhomes_detail(html: str, url: str = "") -> dict | None:
    """Return a clean structured detail dict for one allhomes property, or None."""
    app = _extract_app_props(html)
    if not app:
        return None
    prop = (app.get("body") or {}).get("property")
    if not isinstance(prop, dict):
        return None

    listing = prop.get("listing") or {}
    addr = prop.get("address") or {}
    feats = prop.get("features") or {}
    cad = addr.get("cadastralParcel") or {}
    div = addr.get("division") or {}

    def g(o, *path):
        cur = o
        for p in path:
            cur = cur.get(p) if isinstance(cur, dict) else None
            if cur is None:
                return None
        return cur

    # --- agents (with profile photo + phone) and agency (with logo) ---
    agents_out = []
    for a in prop.get("agents") or []:
        if not isinstance(a, dict):
            continue
        agents_out.append({
            "name": a.get("name", ""),
            "phone": a.get("phone", ""),
            "profile_photo": a.get("profilePhoto", ""),
            "profile_url": a.get("profileUrl", ""),
            "agent_id": a.get("agentId", ""),
        })
    agency_out = {}
    agencies = prop.get("agencies") or []
    if agencies and isinstance(agencies[0], dict):
        ag = agencies[0]
        imgs = ag.get("images") or {}
        logo = ""
        if isinstance(imgs, dict):
            logo = imgs.get("logo") or imgs.get("logoUrl") or imgs.get("agencyLogo") or ""
            if not logo:
                for v in imgs.values():
                    if isinstance(v, str) and v.startswith("http"):
                        logo = v
                        break
        agency_out = {
            "name": ag.get("name", ""),
            "phone": ag.get("phone", ""),
            "logo": logo,
            "logo_background": ag.get("logoBackgroundColour", ""),
            "profile_url": ag.get("profileUrl", ""),
        }

    detail = {
        "url": url or listing.get("url", ""),
        "address": addr.get("formattedFull") or addr.get("line1", ""),
        "suburb": g(div, "name") or "",
        "state": g(addr, "state", "abbreviation") or "",
        "postcode": addr.get("postcode", ""),
        "latitude": g(addr, "coordinates", "lat"),
        "longitude": g(addr, "coordinates", "lon"),
        "property_type": feats.get("propertyType", ""),
        "bedrooms": feats.get("bedrooms"),
        "bathrooms": g(feats, "bathrooms", "total"),
        "ensuites": g(feats, "bathrooms", "ensuites"),
        "parking_total": g(feats, "parking", "total"),
        "garages": g(feats, "parking", "garages"),
        "carports": g(feats, "parking", "carports"),
        "building_size": feats.get("buildingSize"),
        "eer": feats.get("eer"),
        "description": (listing.get("description") or "").strip(),
        "photo_count": g(listing, "media", "photoCount"),
        "image_urls": _img_items(listing.get("media") or {}),
        # --- newly requested top-level fields ---
        "listing_id": listing.get("id", ""),
        "status": listing.get("status", ""),
        "price": listing.get("price") or listing.get("fullPrice", ""),
        "title": listing.get("title", ""),
        "page_views": listing.get("pageViews"),
        "last_updated": listing.get("editDate") or listing.get("relistedDate", ""),
        "listed_date": listing.get("publicVisibleDate", ""),
        "features_list": listing.get("features") or [],
        "inspections": _strip_typename(
            listing.get("inspections") or listing.get("nextInspection") or []
        ),
        "auctions": _strip_typename(listing.get("auctions") or []),
        "agents": agents_out,
        "agency": agency_out,
        # --- the requested sections ---
        "key_details": _strip_typename(g(prop, "derived", "keyDetails") or []),
        "block_information": _strip_typename({
            "block_size": cad.get("blockSize"),
            "block": cad.get("block"),
            "section": cad.get("section"),
            "legal_description": cad.get("legalDescription"),
            "block_map_image": cad.get("blockMapImageUrl"),
            "unimproved_value": g(cad, "unimprovedValue", "formatted"),
            "unimproved_value_history": cad.get("unimprovedValues") or [],
        }),
        "nearby_sales": _strip_typename(addr.get("nearbySales") or {}),
        "sold_comparables": _strip_typename(g(addr, "soldComparables", "nodes") or []),
        "leased_comparables": _strip_typename(g(addr, "leasedComparables", "nodes") or []),
        "risks_and_heritage": _strip_typename(addr.get("perils") or {}),
        "whats_nearby": _strip_typename(addr.get("nearbyPOIs") or []),
        "nbn": _strip_typename(addr.get("nbnDetails") or {}),
        "suburb_information": _strip_typename({
            "name": g(div, "name"),
            "profile": div.get("profile"),
            "statistics": div.get("statistics"),
        }),
        "sale_history": _strip_typename(prop.get("history") or []),
        "value_estimate": _strip_typename(prop.get("valueEstimate") or addr.get("valuation") or {}),
    }
    return detail
