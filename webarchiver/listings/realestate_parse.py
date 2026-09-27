"""Precise parsers for realestate.com.au and allhomes.com.au, derived from the
sites' real embedded data (not guesses):

* realestate: ``window.ArgonautExchange`` -> ``urqlClientCache`` -> a urql cache
  entry whose ``data`` JSON-string contains ``buySearch.results.exact.items[].listing``.
* allhomes: Next.js ``__NEXT_DATA__`` ->
  ``props.pageProps.page.locality.history.nodes`` (sold) and ``...locality.forSale``.
"""
from __future__ import annotations

import json
import re
from typing import Any

_ARG_RE = re.compile(r"window\.ArgonautExchange\s*=\s*(\{.*?\})\s*;?\s*</script>", re.S)


def _date_au(value: str) -> str:
    """'2026-06-11T00:00:00' -> '11/06/2026' (best effort)."""
    if not value or not isinstance(value, str):
        return ""
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", value)
    return f"{m.group(3)}/{m.group(2)}/{m.group(1)}" if m else value


def _size(node: dict | None) -> str:
    if not isinstance(node, dict):
        return ""
    val = node.get("displayValue") or ""
    unit = ""
    su = node.get("sizeUnit")
    if isinstance(su, dict):
        unit = su.get("displayValue", "")
    return f"{val} {unit}".strip()


# ---------------------------------------------------------------- realestate
def parse_realestate_argonaut(html: str) -> list[dict]:
    """Return the list of realestate ``listing`` dicts embedded in the page."""
    m = _ARG_RE.search(html or "")
    if not m:
        return []
    try:
        arg = json.loads(m.group(1))
        web = arg.get("resi-property_listing-experience-web", {})
        cache = json.loads(web.get("urqlClientCache", "{}"))
    except (json.JSONDecodeError, ValueError):
        return []
    listings: list[dict] = []
    for entry in cache.values():
        data = entry.get("data") if isinstance(entry, dict) else None
        if not isinstance(data, str) or "buySearch" not in data:
            continue
        try:
            results = json.loads(data)["buySearch"]["results"]
        except (json.JSONDecodeError, KeyError, ValueError, TypeError):
            continue
        for bucket in ("exact", "surrounding"):
            block = results.get(bucket)
            if isinstance(block, dict):
                for item in block.get("items", []):
                    lst = item.get("listing") if isinstance(item, dict) else None
                    if isinstance(lst, dict):
                        listings.append(lst)
    return listings


def map_realestate_listing(lst: dict) -> dict:
    """Map one realestate listing dict to flat fields."""
    addr = (lst.get("address") or {}).get("display", {}).get("fullAddress", "")
    suburb = (lst.get("address") or {}).get("suburb", "") or ""
    price = (lst.get("price") or {}).get("display", "")
    gf = lst.get("generalFeatures") or {}

    def feat(k: str) -> str:
        v = gf.get(k)
        return str(v.get("value")) if isinstance(v, dict) and v.get("value") is not None else ""

    sizes = lst.get("propertySizes") or {}
    land = _size(sizes.get("land"))
    listers = lst.get("listers") or []
    agent = listers[0].get("name", "") if listers and isinstance(listers[0], dict) else ""
    agency = (lst.get("listingCompany") or {}).get("name", "")
    href = (lst.get("_links") or {}).get("canonical", {}).get("href", "")
    media = lst.get("media") or {}
    imgs = []
    for im in media.get("images", []) or []:
        t = im.get("templatedUrl") if isinstance(im, dict) else None
        if t:
            imgs.append(t.replace("{size}", "800x600"))
    if not imgs:
        main = (media.get("mainImage") or {}).get("templatedUrl")
        if main:
            imgs.append(main.replace("{size}", "800x600"))
    videos = []
    for v in media.get("videos", []) or []:
        if isinstance(v, dict) and v.get("id"):
            tn = v.get("__typename", "")
            if "YouTube" in tn:
                videos.append(f"https://www.youtube.com/watch?v={v['id']}")
            elif v.get("url"):
                videos.append(v["url"])
    for t in media.get("threeDimensionalToursCompat", []) or []:
        if isinstance(t, dict) and t.get("href"):
            videos.append(t["href"])
    ptype = lst.get("propertyType") or ""
    if isinstance(ptype, dict):
        ptype = ptype.get("display") or ptype.get("id") or ""

    # --- rich detail fields (already present in the search store) ---
    building = _size(sizes.get("building"))
    floorplans = []
    for fp in media.get("floorplans", []) or []:
        t = fp.get("templatedUrl") if isinstance(fp, dict) else None
        if t:
            floorplans.append(t.replace("{size}", "1000x750"))
    agents_all = []
    for x in listers:
        if not isinstance(x, dict):
            continue
        nm = x.get("name", "")
        ph = (x.get("phoneNumber") or {}).get("display", "")
        agents_all.append(f"{nm} ({ph})" if ph else nm)
    insp = []
    for ins in lst.get("inspections", []) or []:
        if isinstance(ins, dict):
            lbl = (ins.get("display") or {}).get("longLabel") or ins.get("startTime", "")
            if lbl:
                insp.append(lbl)
    auction = lst.get("auction") or {}
    auction_str = ""
    if isinstance(auction, dict):
        dt = auction.get("dateTime") or {}
        if isinstance(dt, dict):
            auction_str = (dt.get("display") or {}).get("longLabel") or dt.get("value") or ""
        elif isinstance(dt, str):
            auction_str = dt
        if not auction_str:
            auction_str = (auction.get("display") or {}).get("longLabel") or ""
    description = (lst.get("description") or "").replace("<br/>", "\n").replace("<br>", "\n")
    description = re.sub(r"<[^>]+>", "", description).strip()
    return {
        "address": addr, "price": price or "Contact Agent",
        "bedrooms": feat("bedrooms"), "bathrooms": feat("bathrooms"),
        "parking": feat("parkingSpaces"), "property_type": str(ptype).title(),
        "land_size": land, "agent": agent, "agency": agency,
        "listing_url": href, "image_urls": imgs, "video_urls": videos,
        "sale_status": "for_sale",
        "property_id": str(lst.get("id") or ""),
        "description": description, "building_size": building,
        "floorplan_urls": floorplans, "agents": "; ".join(a for a in agents_all if a),
        "inspections": "; ".join(insp), "auction": auction_str,
        "suburb": suburb, "source_site": "realestate",
    }


# ------------------------------------------------------------------ allhomes
def _allhomes_address(addr: dict) -> str:
    if not isinstance(addr, dict):
        return ""
    line1 = addr.get("line1", "")
    div = (addr.get("division") or {}).get("name", "")
    state = (addr.get("state") or {}).get("abbreviation", "")
    pc = addr.get("postcode", "")
    out = line1
    if div:
        out += f" {div}"
    tail = " ".join(p for p in (state, pc) if p)
    if tail:
        out += f", {tail}"
    return out.strip()


def _allhomes_features(feat: dict) -> dict:
    feat = feat or {}

    def total(k: str) -> str:
        v = feat.get(k)
        if isinstance(v, dict):
            return str(v.get("total", "")) if v.get("total") is not None else ""
        return str(v) if v is not None else ""

    return {
        "bedrooms": total("bedrooms"),
        "bathrooms": total("bathrooms"),
        "parking": total("parking"),
        "eer": str(feat.get("eer", "")) if feat.get("eer") is not None else "",
        "property_type": str(feat.get("propertyType", "")).replace("_", " ").title(),
        "building_size": str(feat.get("buildingSize", "")) if feat.get("buildingSize") else "",
    }


def find_allhomes_locality(html_data: Any) -> dict:
    """Return ``props.pageProps.page.locality`` from parsed __NEXT_DATA__."""
    try:
        return html_data["props"]["pageProps"]["page"]["locality"]
    except (KeyError, TypeError):
        return {}


def map_allhomes_sold(node: dict) -> dict:
    addr = _allhomes_address(node.get("address") or {})
    feats = _allhomes_features(node.get("features") or {})
    tr = node.get("transfer") or {}
    listing = node.get("listing") or {}
    price_val = tr.get("price")
    price = f"${price_val:,}" if isinstance(price_val, (int, float)) else "Price withheld"
    label = (tr.get("label") or "").lower()
    if "auction" in label:
        status = "sold_at_auction"
    elif price_val is None:
        status = "withheld"
    else:
        status = "sold"
    block = tr.get("blockSize")
    img = (listing.get("firstImage") or {}).get("imageSrc", "")
    dyn = {}
    for label_k, val in (
        ("Days On Market", listing.get("daysOnMarket")),
        ("Building Size", feats["building_size"]),
        ("Transfer Date", _date_au(tr.get("transferDate") or "")),
        ("Unimproved Value", tr.get("unimprovedValue")),
        ("Purpose", tr.get("purpose")),
        ("Sales Record Source", tr.get("source")),
        ("Sale Label", tr.get("label")),
    ):
        if val not in (None, "", 0):
            dyn[label_k] = str(val)
    return {
        "address": addr, "price": price,
        "bedrooms": feats["bedrooms"], "bathrooms": feats["bathrooms"],
        "parking": feats["parking"], "eer": feats["eer"],
        "property_type": feats["property_type"],
        "land_size": f"{block} m²" if block else "",
        "contract_date": _date_au(tr.get("contractDate") or ""),
        "sale_status": status,
        "listing_url": listing.get("url", ""),
        "image_urls": [img] if img else [],
        "dynamic_data": dyn,
    }


def map_allhomes_forsale(node: dict) -> dict:
    addr = _allhomes_address(node.get("address") or {})
    feats = _allhomes_features(node.get("features") or {})
    listing = node.get("listing") or {}
    agencies = node.get("agencies") or []
    agency = ""
    if agencies and isinstance(agencies[0], dict):
        agency = agencies[0].get("name", "")
    img = (listing.get("firstImage") or {}).get("imageSrc", "")
    return {
        "address": addr, "price": listing.get("price") or "Contact Agent",
        "bedrooms": feats["bedrooms"], "bathrooms": feats["bathrooms"],
        "parking": feats["parking"], "eer": feats["eer"],
        "property_type": feats["property_type"],
        "agency": agency, "sale_status": "for_sale",
        "listing_url": listing.get("url", ""),
        "image_urls": [img] if img else [],
        "source_site": "allhomes",
    }


import re as _re

_AH_SOLD_ROW = _re.compile(
    r"(?P<price>\$[\d,]+|Price withheld)\s+"
    r"(?P<addr>\d+[A-Za-z]?(?:/\d+[A-Za-z]?)?\s+.+?\s+(?:NSW|VIC|QLD|SA|WA|TAS|NT|ACT)\s+\d{4})\s+"
    r"(?P<feats>[\d.\s\-]+?)\s+"
    r"(?P<type>[A-Za-z][A-Za-z ]*?)\s+"
    r"Contract date\s+(?P<date>\d{1,2}/\d{1,2}/\d{4})", _re.S)


def parse_allhomes_sold_dom(html: str) -> list[dict]:
    """Extract sold-history records from the RENDERED allhomes DOM.

    Used for pages 2..N where __NEXT_DATA__ only ever holds page 1. Sold rows are
    identified by the 'Contract date' label (the for-sale promo cards lack it).
    """
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html or "", "lxml")
    out: list[dict] = []
    seen: set[str] = set()
    for div in soup.find_all("div"):
        text = div.get_text(" ", strip=True)
        if "Contract date" not in text or len(text) > 260:
            continue
        m = _AH_SOLD_ROW.search(text)
        if not m:
            continue
        addr = _re.sub(r"\s+", " ", m.group("addr")).replace(" ,", ",").strip()
        if addr in seen:
            continue
        seen.add(addr)
        feats = m.group("feats").split()

        def at(i: int) -> str:
            v = feats[i] if i < len(feats) else ""
            return "" if v == "-" else v

        link = div.find("a", href=True)
        img = div.find("img")
        low = text.lower()
        price = m.group("price")
        status = "withheld" if "withheld" in price.lower() else (
            "sold_at_auction" if "auction" in low else "sold")
        out.append({
            "address": addr, "price": price,
            "bedrooms": at(0), "bathrooms": at(1), "parking": at(2), "eer": at(3),
            "property_type": m.group("type").strip(),
            "contract_date": m.group("date"), "sale_status": status,
            "listing_url": link["href"] if link else "",
            "image_urls": [img.get("src")] if (img and img.get("src") and not img.get("src","").startswith("data:")) else [],
        })
    return out


# ===========================================================================
# allhomes FOR-SALE search (/sale/search) — data lives in __domain_group/APP_PROPS
# under body.initialSearchResults; the page self-describes its search query,
# API endpoint and total page count.
# ===========================================================================
def _img_src(v) -> str:
    if isinstance(v, str):
        url = v
    elif isinstance(v, dict):
        url = v.get("imageSrc") or v.get("url") or v.get("src") or ""
    else:
        return ""
    return _normalize_allhomes_image(url)


_BARE_HASH_IMG = _re_img = __import__("re").compile(r"/([0-9a-f]{32})\.jpg(\?|$)")


def _normalize_allhomes_image(url: str) -> str:
    """Use the production image CDN and request a size variant that exists.

    allhomes gallery URLs come in two broken forms: the "qa" staging host
    (HTTP 500s) and bare-hash filenames with no size suffix (also 500 on the
    CDN). The working form is <hash>_l.jpg on the production host.
    """
    url = url.replace("images.qa.allhomes.com.au", "images.allhomes.com.au")
    m = _BARE_HASH_IMG.search(url)
    if m:  # bare hash, no _l/_m/_t/_110x110 suffix -> request the large variant
        url = url.replace(m.group(1) + ".jpg", m.group(1) + "_l.jpg")
    return url


def _ah_search_img_urls(listing: dict) -> list[str]:
    out = []
    src = _img_src(listing.get("firstImage"))
    if src:
        out.append(src)
    for im in listing.get("additionalImages") or []:
        s = _img_src(im)
        if s:
            out.append(s)
    seen, uniq = set(), []
    for u in out:
        u = u.replace("_m.jpg", "_l.jpg")
        if u not in seen:
            seen.add(u); uniq.append(u)
    return uniq


def map_allhomes_search_item(item: dict) -> dict:
    """Map one allhomes for-sale search result into a flat record dict."""
    listing = item.get("listing") or {}
    addr = item.get("address") or {}
    prop = item.get("property") or {}
    agency = item.get("agency") or {}
    agencies = item.get("agencies") or []
    street = addr.get("street", "")
    suburb = addr.get("division", "")
    state = addr.get("state", "")
    pc = addr.get("postcode", "")
    full = street
    if suburb:
        full += f" {suburb}"
    tail = " ".join(p for p in (state, pc) if p)
    if tail:
        full += f", {tail}"
    bath = prop.get("bathrooms") or {}
    park = prop.get("parking") or {}
    types = prop.get("types") or []
    ptype = (types[0] if types else "").replace("_", " ").title()
    agency_name = agency.get("name") or (
        agencies[0].get("name", "") if agencies and isinstance(agencies[0], dict) else ""
    )
    agency_logo = agency.get("logo") or (
        agencies[0].get("logo", "") if agencies and isinstance(agencies[0], dict) else ""
    )
    return {
        "address": full.strip(),
        "suburb": suburb,
        "price": listing.get("priceLabel") or listing.get("price") or "Contact Agent",
        "bedrooms": str(prop.get("beds", "")) if prop.get("beds") is not None else "",
        "bathrooms": str(bath.get("total", prop.get("baths", "")) or ""),
        "parking": str(park.get("total", prop.get("cars", "")) or ""),
        "eer": str(prop.get("eer", "")) if prop.get("eer") is not None else "",
        "land_size": str(prop.get("size", "")) if prop.get("size") else "",
        "property_type": ptype,
        "listing_id": str(listing.get("id", "")),
        "sale_status": "for_sale",
        "agency": agency_name,
        "agency_logo": agency_logo,
        "listing_url": listing.get("url", ""),
        "image_urls": _ah_search_img_urls(listing),
        "source_site": "allhomes",
    }


def parse_allhomes_search(html: str) -> dict:
    """Return {'items':[...], 'total_pages':int, 'total_hits':int,
    'query':dict, 'search_endpoint':str} from a /sale/search page, or {} ."""
    from webarchiver.listings.allhomes_detail import _extract_app_props

    app = _extract_app_props(html)
    if not app:
        return {}
    body = app.get("body") or {}
    items = body.get("initialSearchResults")
    if not isinstance(items, list):
        return {}
    endpoints = body.get("endpoints") or {}
    return {
        "items": items,
        "total_pages": body.get("totalPages") or body.get("numPages") or 1,
        "total_hits": body.get("totalHits") or 0,
        "query": body.get("query") or {},
        "search_endpoint": endpoints.get("searchApiEndpoint", ""),
    }


def extract_search_results_from_response(text: str) -> list:
    """Pull the list of search-result items out of a /wsvc/search response.

    The live envelope key isn't guaranteed, so we search recursively for the
    first list whose elements look like search items (a dict with 'listing' and
    'address'). Falls back to common keys. Returns [] if nothing matches.
    """
    import json as _json

    try:
        data = _json.loads(text)
    except (ValueError, TypeError):
        return []

    def looks_like_items(lst) -> bool:
        return (
            isinstance(lst, list) and lst and isinstance(lst[0], dict)
            and "listing" in lst[0] and "address" in lst[0]
        )

    # 1) common explicit keys
    if isinstance(data, dict):
        for key in ("initialSearchResults", "searchResults", "results", "listings", "items"):
            v = data.get(key)
            if looks_like_items(v):
                return v
            if isinstance(v, dict):
                for k2 in ("results", "items", "nodes", "listings"):
                    if looks_like_items(v.get(k2)):
                        return v[k2]

    # 2) recursive scan for the first item-shaped list
    found: list = []

    def walk(o):
        nonlocal found
        if found:
            return
        if looks_like_items(o):
            found = o
            return
        if isinstance(o, dict):
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(data)
    return found
