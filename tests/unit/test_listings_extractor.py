from webarchiver.listings.extractor import (
    GenericListingExtractor,
    RealestateExtractor,
    get_extractor,
)

CARD = """
<article class="residential-card"><div class="residential-card__content">
  <span class="property-price">$850,000</span>
  <h2><a href="/property-house-act-florey-145012345">12 Banksia St, Florey ACT 2615</a></h2>
  <div class="general-features">
    <span aria-label="3 bedrooms">3</span>
    <span aria-label="2 bathrooms">2</span>
    <span aria-label="1 car space">1</span>
  </div>
  <span class="residential-card__property-type">House</span>
  <div class="branding"><img class="agency-logo" alt="Ray White Canberra"></div>
  <img src="https://img.example/main.jpg" alt="photo">
</div></article>
"""


def test_autoselect_realestate_by_host():
    assert isinstance(get_extractor("www.realestate.com.au"), RealestateExtractor)
    assert isinstance(get_extractor("example.com"), GenericListingExtractor)


def test_realestate_card_fields():
    recs = RealestateExtractor().extract(CARD, "https://www.realestate.com.au/buy/in-canberra/list-1")
    assert len(recs) == 1
    r = recs[0]
    assert r.address.startswith("12 Banksia St")
    assert r.price == "$850,000"
    assert (r.bedrooms, r.bathrooms, r.parking) == ("3", "2", "1")
    assert r.property_type == "House"
    assert r.agency == "Ray White Canberra"
    assert r.listing_url.endswith("145012345")
    # property photo chosen, not the agency logo
    assert r.image_urls == ["https://img.example/main.jpg"]


def test_handles_empty_html():
    assert RealestateExtractor().extract("", "https://x.com/") == []
