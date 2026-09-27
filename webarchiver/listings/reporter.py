"""Write structured listing reports (CSV / Excel / JSON)."""
from __future__ import annotations

import logging
from pathlib import Path

from webarchiver.listings.models import ListingRecord
from webarchiver.report.exporters import to_csv, to_excel, to_json

_COLS = [
    "Address", "Suburb", "Price", "Bedrooms", "Bathrooms", "Parking", "Property Type",
    "Property ID", "Listing ID", "Land Size", "Building Size", "EER",
    "Contract Date", "Listed Date", "Last Updated", "Days On Market", "Page Views",
    "Sale Status", "Agent", "Agents", "Agency", "Agent Photo", "Agency Logo",
    "Property Features", "Inspections", "Auction", "Source Site",
    "Listing URL", "Image URLs", "Local Image Paths", "Video URLs", "Floorplan URLs",
    "Description", "Source Page",
]


def write_listings(
    listings: list[ListingRecord],
    root_dir: Path,
    *,
    export_csv: bool = True,
    export_excel: bool = True,
    export_json: bool = True,
    logger: logging.Logger | None = None,
) -> None:
    """Write ``listings`` to the site root in the requested formats."""
    log = logger or logging.getLogger("webarchiver.listings")
    rows = [r.as_row() for r in listings]
    root = Path(root_dir)

    all_keys = set(_COLS)
    for row in rows:
        all_keys.update(row.keys())
    final_cols = _COLS + sorted(list(all_keys - set(_COLS)))

    if export_csv:
        to_csv(rows, root / "listings.csv", columns=final_cols)
    if export_excel:
        to_excel({"listings": rows}, root / "listings.xlsx", columns={"listings": final_cols})
    if export_json:
        to_json(rows, root / "listings.json")
    log.info("Listings extracted: %d", len(rows))
