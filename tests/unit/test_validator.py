from webarchiver.listings.models import ListingRecord
from webarchiver.listings.validator import DataValidator, validate_all


def _rec(**kw):
    base = dict(source_page="p", address="12 Banksia St, Florey ACT 2615", price="$850,000",
                bedrooms="3", bathrooms="2", parking="1", property_type="House",
                image_urls=["http://i/x.jpg"], listing_url="http://r/1")
    base.update(kw)
    return ListingRecord(**base)


def test_good_record_passes_high_score():
    res = DataValidator().validate(_rec())
    assert not res.rejected
    assert res.score >= 80
    assert res.state == "ACT" and res.postcode == "2615"


def test_autofix_bedrooms_and_property_type():
    rec = _rec(bedrooms="3 beds", property_type="house")
    DataValidator().validate(rec)
    assert rec.bedrooms == "3"
    assert rec.property_type == "House"


def test_placeholder_address_rejected():
    res = DataValidator().validate(_rec(address="N/A", price=""))
    assert res.rejected
    assert any("address" in e for e in res.errors)


def test_validate_all_filters_rejected():
    accepted, results = validate_all([_rec(), _rec(address="N/A", price="")], min_quality_score=30)
    assert len(accepted) == 1
    assert len(results) == 2
