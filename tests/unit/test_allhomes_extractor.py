import json

from webarchiver.listings.extractor import AllhomesExtractor, get_extractor


def _nuxt(records):
    return ('<html><head><script id="__NUXT_DATA__" type="application/json">'
            + json.dumps({"data": {"research": {"salesHistory": {"results": records}}}})
            + "</script></head></html>")


def test_auto_selects_allhomes():
    assert isinstance(get_extractor("www.allhomes.com.au"), AllhomesExtractor)
    assert isinstance(get_extractor("x.com", "allhomes"), AllhomesExtractor)


def test_sold_history_from_json():
    recs = AllhomesExtractor().extract(_nuxt([
        {"address": {"display": {"fullAddress": "4 Skertchly Place Florey, ACT 2615"}},
         "soldPrice": "$840,000", "bedrooms": 3, "bathrooms": 1, "parkingSpaces": 1,
         "energyEfficiencyRating": 2, "propertyType": "House", "contractDate": "05/06/2026"},
    ]), "https://www.allhomes.com.au/research/florey")
    assert len(recs) == 1
    r = recs[0]
    assert r.price == "$840,000"
    assert r.contract_date == "05/06/2026"
    assert r.eer == "2"
    assert r.sale_status == "sold"


def test_price_withheld_status():
    recs = AllhomesExtractor().extract(_nuxt([
        {"address": {"fullAddress": "13 Clavert Place Florey, ACT 2615"},
         "displayPrice": "Price withheld", "bedrooms": 2, "contractDate": "28/05/2026"},
    ]), "https://www.allhomes.com.au/research/florey")
    assert recs[0].sale_status == "withheld"


def test_dom_fallback_when_no_json():
    dom = ('<html><body><div class="card"><span>$1,285,000</span>'
           '<div>19 Boswell Crescent Florey, ACT 2615</div><span>House</span>'
           '<div>Contract date 21/03/2026</div></div></body></html>')
    recs = AllhomesExtractor().extract(dom, "https://www.allhomes.com.au/research/florey")
    assert recs and recs[0].address.startswith("19 Boswell Crescent")
    assert recs[0].price == "$1,285,000"
    assert recs[0].contract_date == "21/03/2026"


def test_sold_history_from_rendered_dom():
    # Rendered allhomes sold rows are identified by 'Contract date'.
    dom = ('<html><body>'
           '<div><a href="https://www.allhomes.com.au/4-skertchly-place-florey-act-2615">x</a>'
           '$840,000 4 Skertchly Place Florey, ACT 2615 3 1 1 2 House Contract date 05/06/2026 View full details</div>'
           '<div>Price withheld 13 Clavert Place Florey, ACT 2615 2 1 2 1 House '
           'Contract date 28/05/2026 View full details</div>'
           '</body></html>')
    from webarchiver.listings.extractor import AllhomesExtractor
    recs = AllhomesExtractor().extract(dom, "https://www.allhomes.com.au/research/florey-act-2615")
    assert len(recs) == 2
    by = {r.address.split()[0]: r for r in recs}
    assert by["4"].price == "$840,000" and by["4"].contract_date == "05/06/2026"
    assert by["4"].bedrooms == "3" and by["4"].eer == "2" and by["4"].sale_status == "sold"
    assert by["13"].sale_status == "withheld"
    assert by["4"].listing_url.endswith("4-skertchly-place-florey-act-2615")
