from webarchiver.listings.models import ListingRecord


def test_sold_fields_in_row():
    r = ListingRecord(source_page="p", address="1 St ACT 2600", price="$1",
                      eer="3", contract_date="01/01/2026", sale_status="sold", land_size="500m2")
    row = r.as_row()
    for col in ("Land Size", "EER", "Contract Date", "Sale Status"):
        assert col in row
    assert row["Sale Status"] == "sold"
    assert row["EER"] == "3"


def test_dynamic_data_columns_merge():
    r = ListingRecord(source_page="p", address="1 St ACT 2600")
    r.dynamic_data["Pool"] = "Yes"
    assert r.as_row()["Pool"] == "Yes"
