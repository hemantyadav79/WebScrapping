"""Pluggable property-listing extractors.

Each extractor turns rendered search-results HTML into structured
:class:`ListingRecord` objects. The interface is open for extension: add a new
subclass and register it in :func:`get_extractor` to support another portal,
without touching the crawl or pipeline code (OCP / DIP).
"""
from __future__ import annotations

import abc
import re

from bs4 import BeautifulSoup, Tag

from webarchiver.core.url_utils import normalize_url, resolve_url
from webarchiver.listings.hydration import (
    deep_scalar, first as hy_first, iter_listings, iter_property_objects, parse_hydration,
)
from webarchiver.listings.models import ListingRecord

_WS = re.compile(r"\s+")
_NUM = re.compile(r"\d+")


def _clean(text: str | None) -> str:
    if not text:
        return ""
    return _WS.sub(" ", text).strip()


def _record_from_map(m: dict, base_url: str) -> ListingRecord:
    """Build a ListingRecord from a flat field dict, resolving URLs/images."""
    rec = ListingRecord(source_page=base_url)
    rec.address = m.get("address", "") or ""
    rec.price = m.get("price", "") or ""
    rec.bedrooms = m.get("bedrooms", ""); rec.bathrooms = m.get("bathrooms", "")
    rec.parking = m.get("parking", ""); rec.property_type = m.get("property_type", "")
    rec.land_size = m.get("land_size", ""); rec.eer = m.get("eer", "")
    rec.contract_date = m.get("contract_date", ""); rec.sale_status = m.get("sale_status", "")
    rec.agent = m.get("agent", ""); rec.agency = m.get("agency", "")
    rec.property_id = m.get("property_id", ""); rec.description = m.get("description", "")
    rec.building_size = m.get("building_size", ""); rec.agents = m.get("agents", "")
    rec.inspections = m.get("inspections", ""); rec.auction = m.get("auction", "")
    rec.suburb = m.get("suburb", ""); rec.listing_id = m.get("listing_id", "")
    rec.agency_logo = m.get("agency_logo", ""); rec.agent_photo = m.get("agent_photo", "")
    rec.page_views = m.get("page_views", ""); rec.last_updated = m.get("last_updated", "")
    rec.listed_date = m.get("listed_date", ""); rec.days_on_market = m.get("days_on_market", "")
    rec.property_features = m.get("property_features", ""); rec.source_site = m.get("source_site", "")
    for u in m.get("floorplan_urls", []) or []:
        r = resolve_url(base_url, u)
        rec.floorplan_urls.append(normalize_url(r) if r else u)
    href = m.get("listing_url", "")
    if href:
        r = resolve_url(base_url, href); rec.listing_url = normalize_url(r) if r else href
    for u in m.get("image_urls", []) or []:
        r = resolve_url(base_url, u)
        rec.image_urls.append(normalize_url(r) if r else u)
    for u in m.get("video_urls", []) or []:
        r = resolve_url(base_url, u)
        rec.video_urls.append(normalize_url(r) if r else u)
    rec.dynamic_data.update(m.get("dynamic_data", {}) or {})
    return rec


class ListingExtractor(abc.ABC):
    """Base class for site-specific listing extractors."""

    name: str = "base"

    @abc.abstractmethod
    def extract(self, html: str, base_url: str) -> list[ListingRecord]:
        """Return all listing records found in ``html``."""

    @staticmethod
    def _first(card: Tag, selectors: list[str]) -> Tag | None:
        for sel in selectors:
            found = card.select_one(sel)
            if found is not None:
                return found
        return None


class RealestateExtractor(ListingExtractor):
    """Extractor for realestate.com.au ``residential-card`` results."""

    name = "realestate"

    # Top-level result cards (not the nested __content / __details blocks).
    _CARD_SELECTORS = [
        "article.residential-card",
        "div.residential-card",
        '[class*="residential-card"][class*="card"]',
        "article[class*='residential-card']",
    ]

    def extract(self, html: str, base_url: str) -> list[ListingRecord]:
        # Primary: window.ArgonautExchange (the real realestate.com.au data source).
        from webarchiver.listings.realestate_parse import (
            map_realestate_listing, parse_realestate_argonaut,
        )
        argo = parse_realestate_argonaut(html)
        if argo:
            out = [_record_from_map(map_realestate_listing(l), base_url) for l in argo]
            out = [r for r in out if r.address]
            if out:
                return out
        # Secondary: legacy __NEXT_DATA__ hydration JSON (older page versions).
        hydrated = self._from_hydration(html, base_url)
        if hydrated:
            return hydrated
        # Fallback: parse the rendered DOM cards (then Ollama, in the pipeline).
        soup = BeautifulSoup(html or "", "lxml")
        cards: list[Tag] = []
        for sel in self._CARD_SELECTORS:
            cards = soup.select(sel)
            if cards:
                break

        records: list[ListingRecord] = []
        seen_addresses: set[str] = set()
        for card in cards:
            for rec in self._parse_card(card, base_url):
                if not rec.address and not rec.price:
                    continue
                key = rec.address or rec.listing_url
                if key in seen_addresses and not hasattr(rec, '_is_child'):
                    continue
                seen_addresses.add(key)
                records.append(rec)
        return records

    def _from_hydration(self, html: str, base_url: str) -> list[ListingRecord]:
        data = parse_hydration(html)
        if data is None:
            return []
        records: list[ListingRecord] = []
        seen: set[str] = set()
        for obj in iter_listings(data):
            rec = self._map_hydrated(obj, base_url)
            if not rec.address or len(rec.address.strip()) < 4:
                continue  # no real address -> let DOM / Ollama try instead
            key = rec.listing_url or rec.address
            if key in seen:
                continue
            seen.add(key)
            records.append(rec)
        return records

    @staticmethod
    def _map_hydrated(obj: dict, base_url: str) -> ListingRecord:
        rec = ListingRecord(source_page=base_url)
        rec.address = hy_first(obj, [
            ["address", "display", "fullAddress"], ["address", "display"],
            ["address", "fullAddress"], ["address"],
        ]) or deep_scalar(obj, ("address", "fulladdress"))
        rec.price = hy_first(obj, [
            ["price", "display"], ["price", "text"], ["priceText"], ["price"],
        ]) or deep_scalar(obj, ("price",)) or "Contact Agent"
        rec.bedrooms = hy_first(obj, [
            ["generalFeatures", "bedrooms", "value"], ["bedrooms", "value"], ["bedrooms"],
        ])
        rec.bathrooms = hy_first(obj, [
            ["generalFeatures", "bathrooms", "value"], ["bathrooms", "value"], ["bathrooms"],
        ])
        rec.parking = hy_first(obj, [
            ["generalFeatures", "parkingSpaces", "value"], ["parkingSpaces", "value"],
            ["carspaces"], ["parking"],
        ])
        rec.property_type = hy_first(obj, [
            ["propertyType", "display"], ["propertyType"],
        ])
        href = hy_first(obj, [
            ["_links", "canonical", "href"], ["listingUrl"], ["url"], ["links", "canonical"],
        ])
        if href:
            resolved = resolve_url(base_url, href)
            rec.listing_url = normalize_url(resolved) if resolved else ""
        img = hy_first(obj, [
            ["media", "mainImage", "templatedUrl"], ["mainImage", "templatedUrl"],
            ["images", "0", "templatedUrl"], ["media", "0", "url"], ["image"],
        ])
        # Some templated URLs contain a {size} placeholder.
        img = img.replace("{size}", "800x600").replace("{resize}", "800x600") if img else img
        if img:
            resolved = resolve_url(base_url, img)
            if resolved:
                rec.image_urls.append(normalize_url(resolved))
        rec.agent = hy_first(obj, [["listers", "0", "name"], ["agent", "name"]])
        rec.agency = hy_first(obj, [["listers", "0", "agencyName"], ["agency", "name"], ["agencyName"]])
        return rec

    def _parse_card(self, card: Tag, base_url: str) -> list[ListingRecord]:
        rec = ListingRecord(source_page=base_url)

        addr = self._first(card, ["h2 a", "address", "a.details-link",
                                  "a[class*='details-link']", "h2"])
        if addr is not None:
            rec.address = _clean(addr.get_text())
            href = addr.get("href") if addr.name == "a" else None
            if not href:
                link = card.select_one("a[href]")
                href = link.get("href") if link else None
            resolved = resolve_url(base_url, href or "")
            rec.listing_url = normalize_url(resolved) if resolved else ""

        price = self._first(card, ["[class*='property-price']", "[class*='price']"])
        if price is None:
            price = card.find(string=re.compile(r"\$"))
            rec.price = _clean(str(price)) if price else "Contact Agent"
        else:
            rec.price = _clean(price.get_text()) or "Contact Agent"

        rec.bedrooms, rec.bathrooms, rec.parking = self._features(card)

        ptype = self._first(card, ["[class*='property-type']", "[class*='residential-card__property-type']"])
        rec.property_type = _clean(ptype.get_text()) if ptype else ""

        agent = self._first(card, ["[class*='agent__name']", "[class*='agent-name']"])
        rec.agent = _clean(agent.get_text()) if agent else ""
        brand = self._first(card, ["[class*='branding'] img", "img[class*='agency']"])
        if brand is not None:
            rec.agency = _clean(brand.get("alt", ""))
            
        avatar = self._first(card, ["img[class*='Avatar']", ".agent img"])
        if avatar is not None:
            avatar_src = avatar.get("src") or avatar.get("data-src")
            if avatar_src:
                rec.dynamic_data["Agent Avatar URL"] = resolve_url(base_url, avatar_src) or ""

        rec.image_urls = self._property_images(card, base_url)
        
        # Handle child listings
        records = [rec]
        for child in card.select(".child-listing"):
            child_rec = ListingRecord(source_page=base_url, listing_url=rec.listing_url)
            setattr(child_rec, '_is_child', True)
            
            # Base agent/agency/address logic inherited
            child_rec.agent = rec.agent
            child_rec.agency = rec.agency
            
            title_node = child.select_one(".child-listing__title")
            if title_node:
                child_rec.address = _clean(title_node.get_text()) + f" (at {rec.address})"
            else:
                child_rec.address = rec.address
                
            price_node = child.select_one(".child-listing__price")
            if price_node:
                child_rec.price = _clean(price_node.get_text())
                
            child_rec.bedrooms, child_rec.bathrooms, child_rec.parking = self._features(child)
            
            img_node = child.select_one("img.child-listing__image, img")
            if img_node:
                c_src = img_node.get("src") or img_node.get("data-src")
                if c_src:
                    c_res = resolve_url(base_url, c_src)
                    if c_res:
                        child_rec.image_urls.append(normalize_url(c_res))
            
            records.append(child_rec)

        return records

    @staticmethod
    def _property_images(card: Tag, base_url: str) -> list[str]:
        """Pick all property photos, skipping agency/branding logo images."""
        urls = []
        seen = set()
        
        # Check carousel first
        carousel = card.select_one(".carousel, .residential-card__images")
        container = carousel if carousel else card
        
        for el in container.select("img, [data-url], [data-testid='PropertyImage']"):
            src = el.get("src") or el.get("data-src") or el.get("data-url") or ""
            if not src:
                continue
            cls = " ".join(el.get("class", [])).lower()
            if "logo" in cls or "agency" in cls or "brand" in cls or "avatar" in cls:
                continue
            if el.find_parent(class_=re.compile(r"brand|agency|logo|agent", re.I)) is not None:
                continue
            if "placeholder" in src.lower():
                continue
            resolved = resolve_url(base_url, src)
            if resolved:
                norm = normalize_url(resolved)
                if norm not in seen:
                    seen.add(norm)
                    urls.append(norm)
        return urls

    @staticmethod
    def _features(card: Tag) -> tuple[str, str, str]:
        """Best-effort bed/bath/car extraction from aria-labels or feature spans."""
        beds = baths = cars = ""
        # Preferred: elements carrying an aria-label like "3 bedrooms".
        for el in card.select("[aria-label]"):
            label = el.get("aria-label", "").lower()
            num = _NUM.search(label)
            value = num.group(0) if num else _clean(el.get_text())
            if not value:
                continue
            if "bed" in label and not beds:
                beds = value
            elif "bath" in label and not baths:
                baths = value
            elif ("car" in label or "park" in label) and not cars:
                cars = value
        # Fallback: generic feature list with icons + adjacent numbers.
        if not (beds or baths or cars):
            feats = card.select("[class*='general-features'] [class*='feature'], "
                                "[class*='property-features'] li")
            nums = [_clean(f.get_text()) for f in feats]
            nums = [n for n in nums if _NUM.search(n)]
            if len(nums) >= 1:
                beds = nums[0]
            if len(nums) >= 2:
                baths = nums[1]
            if len(nums) >= 3:
                cars = nums[2]
        return beds, baths, cars


class GenericListingExtractor(ListingExtractor):
    """Heuristic fallback: any element whose class contains 'card' or 'listing'."""

    name = "generic"

    def extract(self, html: str, base_url: str) -> list[ListingRecord]:
        soup = BeautifulSoup(html or "", "lxml")
        cards = soup.select("[class*='listing-card'], [class*='property-card'], "
                            "article[class*='card']")
        records: list[ListingRecord] = []
        for card in cards:
            rec = ListingRecord(source_page=base_url)
            heading = card.select_one("h1, h2, h3, address, a[href]")
            if heading is not None:
                rec.address = _clean(heading.get_text())
                if heading.name == "a":
                    resolved = resolve_url(base_url, heading.get("href", ""))
                    rec.listing_url = normalize_url(resolved) if resolved else ""
            price = card.find(string=re.compile(r"\$"))
            rec.price = _clean(str(price)) if price else ""
            img = card.select_one("img")
            if img is not None:
                resolved = resolve_url(base_url, img.get("src") or img.get("data-src") or "")
                if resolved:
                    rec.image_urls.append(normalize_url(resolved))
            if rec.address or rec.price:
                records.append(rec)
        return records



class AllhomesExtractor(ListingExtractor):
    """Extractor for allhomes.com.au — sold sales-history AND for-sale listings.

    allhomes is a Nuxt/Domain app, so structured data is usually embedded as
    JSON. We read that first (robust to CSS changes), then fall back to a DOM
    text heuristic. The pipeline's Ollama fallback covers anything left.
    """

    name = "allhomes"

    _DETAIL_KEYS = ("price", "solddate", "contractdate", "saledate",
                    "bedrooms", "propertytype", "generalfeatures")
    _ADDR_RE = re.compile(
        r"(?<![\d,$])\b\d{1,5}[A-Za-z]?(?:/\d{1,5}[A-Za-z]?)?\s+"
        r"[A-Za-z][\w'.\- ]+?,?\s*(?:NSW|VIC|QLD|SA|WA|TAS|NT|ACT)\s+\d{4}")
    _PRICE_RE = re.compile(r"\$[\d,]+|price withheld", re.I)
    _DATE_RE = re.compile(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b")
    _NUM = re.compile(r"\d+")

    def extract(self, html: str, base_url: str) -> list[ListingRecord]:
        from webarchiver.listings.realestate_parse import (
            find_allhomes_locality, map_allhomes_forsale, map_allhomes_search_item,
            map_allhomes_sold, parse_allhomes_search, parse_allhomes_sold_dom,
        )
        # Primary (for-sale search /sale/search): APP_PROPS.initialSearchResults.
        search = parse_allhomes_search(html)
        if search.get("items"):
            out = [_record_from_map(map_allhomes_search_item(it), base_url)
                   for it in search["items"]]
            out = [r for r in out if r.address]
            if out:
                return out
        # Primary (sold history): rendered-DOM sold rows (works on ANY page; __NEXT_DATA__ is
        # always page 1, so the DOM is the only source for pages 2..N).
        dom_sold = parse_allhomes_sold_dom(html)
        if dom_sold:
            return [_record_from_map(m, base_url) for m in dom_sold]
        # Secondary: Next.js structure (page-1 sold + forSale preview).
        data = parse_hydration(html)
        out: list[ListingRecord] = []
        if data is not None:
            loc = find_allhomes_locality(data)
            for node in (loc.get("history") or {}).get("nodes", []) or []:
                m = map_allhomes_sold(node)
                if m["address"]:
                    out.append(_record_from_map(m, base_url))
            for node in loc.get("forSale", []) or []:
                m = map_allhomes_forsale(node)
                if m["address"]:
                    out.append(_record_from_map(m, base_url))
        if out:
            return out
        # Fallbacks: generic JSON record search, then DOM heuristic.
        records = self._from_json(html, base_url)
        if records:
            return records
        return self._from_dom(html, base_url)

    # ---- JSON path ----------------------------------------------------
    def _from_json(self, html: str, base_url: str) -> list[ListingRecord]:
        data = parse_hydration(html)
        if data is None:
            return []
        out: list[ListingRecord] = []
        seen: set[str] = set()
        for obj in iter_property_objects(data, self._DETAIL_KEYS):
            rec = self._map_json(obj, base_url)
            if not rec.address or len(rec.address.strip()) < 4:
                continue
            key = (rec.address + "|" + rec.contract_date).lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(rec)
        return out

    @staticmethod
    def _map_json(obj: dict, base_url: str) -> ListingRecord:
        rec = ListingRecord(source_page=base_url)
        rec.address = (hy_first(obj, [["address", "display", "fullAddress"],
                                      ["address", "fullAddress"], ["address"]])
                       or deep_scalar(obj, ("address", "fulladdress")))
        rec.price = (deep_scalar(obj, ("soldprice", "displayprice", "price"))
                     or "Price withheld")
        rec.bedrooms = deep_scalar(obj, ("bedroom", "beds"))
        rec.bathrooms = deep_scalar(obj, ("bathroom", "baths"))
        rec.parking = deep_scalar(obj, ("parking", "carspace", "garage"))
        rec.eer = deep_scalar(obj, ("energy", "eer"))
        rec.land_size = deep_scalar(obj, ("blocksize", "landarea", "landsize", "blockarea"))
        rec.property_type = deep_scalar(obj, ("propertytype", "dwellingtype"))
        rec.contract_date = deep_scalar(obj, ("contractdate", "solddate", "saledate"))
        # Rich sold-history extras (allhomes expanded record) -> dynamic columns.
        for label, parts in (
            ("Transfer Date", ("transferdate",)),
            ("Listed Date", ("listeddate", "datelisted")),
            ("Days On Market", ("daysonmarket",)),
            ("Unimproved Value", ("unimprovedvalue",)),
            ("Unimproved Value Ratio", ("unimprovedvalueratio", "uvratio")),
            ("Building Size", ("buildingsize", "floorarea")),
            ("Purpose", ("purpose",)),
            ("Sales Record Source", ("salesrecordsource", "recordsource")),
            ("Sale Method", ("salemethod", "saletype")),
        ):
            val = deep_scalar(obj, parts)
            if val:
                rec.dynamic_data[label] = val
        href = deep_scalar(obj, ("listingurl", "url", "slug", "href"))
        if href:
            resolved = resolve_url(base_url, href)
            rec.listing_url = normalize_url(resolved) if resolved else ""
        img = deep_scalar(obj, ("image", "photo", "thumbnail"))
        if img:
            resolved = resolve_url(base_url, img)
            if resolved:
                rec.image_urls.append(normalize_url(resolved))
        rec.sale_status = AllhomesExtractor._status(rec)
        return rec

    @staticmethod
    def _status(rec: ListingRecord) -> str:
        method = rec.dynamic_data.get("Sale Method", "").lower()
        if "auction" in method:
            return "sold_at_auction"
        if "withheld" in rec.price.lower():
            return "withheld"
        if rec.contract_date:
            return "sold"
        return "for_sale"

    # ---- DOM heuristic fallback --------------------------------------
    def _from_dom(self, html: str, base_url: str) -> list[ListingRecord]:
        soup = BeautifulSoup(html or "", "lxml")
        out: list[ListingRecord] = []
        seen: set[str] = set()
        # Treat any element whose text holds both an address and a price/withheld
        # as a record card; pick the smallest such container to avoid nesting.
        for el in soup.find_all(["article", "li", "div"]):
            text = el.get_text(" ", strip=True)
            if len(text) > 600:
                continue
            addr_m = self._ADDR_RE.search(text)
            price_m = self._PRICE_RE.search(text)
            if not addr_m or not price_m:
                continue
            address = _clean(addr_m.group(0))
            if address.lower() in seen:
                continue
            seen.add(address.lower())
            rec = ListingRecord(source_page=base_url, address=address)
            rec.price = _clean(price_m.group(0))
            date_m = self._DATE_RE.search(text)
            rec.contract_date = date_m.group(0) if date_m else ""
            tl = text.lower()
            for t in ("house", "townhouse", "apartment", "unit", "villa", "land", "duplex"):
                if t in tl:
                    rec.property_type = t.title()
                    break
            img = el.find("img")
            if img is not None:
                resolved = resolve_url(base_url, img.get("src") or img.get("data-src") or "")
                if resolved:
                    rec.image_urls.append(normalize_url(resolved))
            rec.sale_status = self._status(rec)
            out.append(rec)
        return out


def get_extractor(host: str, source: str = "auto") -> ListingExtractor:
    """Return the extractor for ``source`` (or auto-detect from ``host``)."""
    source = (source or "auto").lower()
    if source == "ollama":
        from webarchiver.llm.ollama_extractor import OllamaListingExtractor
        return OllamaListingExtractor()
    if source == "allhomes" or (source == "auto" and "allhomes.com.au" in host):
        return AllhomesExtractor()
    if source == "realestate" or (source == "auto" and "realestate.com.au" in host):
        return RealestateExtractor()
    return GenericListingExtractor()
