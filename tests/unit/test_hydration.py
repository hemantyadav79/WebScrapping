import json

from webarchiver.listings.extractor import RealestateExtractor
from webarchiver.listings.hydration import iter_listings, parse_hydration


def _next_data(listings):
    return {"props": {"pageProps": {"results": listings}}}


def _listing(lid, addr):
    return {
        "listingId": lid, "_links": {"canonical": {"href": f"/property-{lid}"}},
        "address": {"display": {"fullAddress": addr}},
        "price": {"display": "$800,000"},
        "generalFeatures": {"bedrooms": {"value": 3}, "bathrooms": {"value": 2},
                            "parkingSpaces": {"value": 1}},
        "propertyType": {"display": "House"},
        "media": {"mainImage": {"templatedUrl": "https://img/{size}/" + lid + ".jpg"}},
        "listers": [{"name": "Jane", "agencyName": "Ray White"}],
    }


def test_parse_and_iter():
    data = _next_data([_listing("1", "12 St ACT 2615"), _listing("2", "9 Rd ACT 2617")])
    html = f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script>'
    parsed = parse_hydration(html)
    assert parsed is not None
    assert len(list(iter_listings(parsed))) == 2


def test_extractor_prefers_hydration_over_dom():
    data = _next_data([_listing("1", "12 Banksia St, Florey ACT 2615")])
    html = f'<html><script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script><body>no cards</body></html>'
    recs = RealestateExtractor().extract(html, "https://www.realestate.com.au/buy/in-canberra/list-1")
    assert len(recs) == 1
    r = recs[0]
    assert r.address.startswith("12 Banksia")
    assert (r.bedrooms, r.bathrooms, r.parking) == ("3", "2", "1")
    assert r.image_urls == ["https://img/800x600/1.jpg"]  # {size} placeholder filled
    assert r.listing_url.endswith("property-1")


def test_no_hydration_returns_none():
    assert parse_hydration("<html><body>nothing</body></html>") is None
