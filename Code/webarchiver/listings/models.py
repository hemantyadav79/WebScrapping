"""Structured property-listing record extracted from a result page."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class ListingRecord:
    """A single property listing parsed from a search-results card."""

    source_page: str
    listing_url: str = ""
    address: str = ""
    suburb: str = ""
    price: str = ""
    bedrooms: str = ""
    bathrooms: str = ""
    parking: str = ""
    property_type: str = ""
    property_id: str = ""          # realestate listing id / generic id
    listing_id: str = ""           # allhomes listing id
    description: str = ""
    land_size: str = ""
    building_size: str = ""
    eer: str = ""                 # energy efficiency rating (allhomes sold history)
    contract_date: str = ""       # sale/contract date (sold history)
    listed_date: str = ""         # date listing went live
    last_updated: str = ""        # listing last-updated / edit date
    days_on_market: str = ""      # derived where available
    page_views: str = ""          # "Viewed N times" / "N page views"
    sale_status: str = ""         # "for_sale" | "sold" | "withheld" | ""
    agent: str = ""
    agents: str = ""              # all agents: "Name (phone); ..."
    agency: str = ""
    agent_photo: str = ""         # first agent profile photo URL
    agency_logo: str = ""         # agency logo URL
    property_features: str = ""   # "; "-joined feature list
    inspections: str = ""
    auction: str = ""
    source_site: str = ""         # "realestate" | "allhomes" | ...
    image_urls: list[str] = field(default_factory=list)
    local_image_paths: list[str] = field(default_factory=list)
    video_urls: list[str] = field(default_factory=list)
    floorplan_urls: list[str] = field(default_factory=list)
    dynamic_data: dict[str, str] = field(default_factory=dict)

    def as_row(self) -> dict[str, str]:
        row = {
            "Address": self.address,
            "Suburb": self.suburb,
            "Price": self.price,
            "Bedrooms": self.bedrooms,
            "Bathrooms": self.bathrooms,
            "Parking": self.parking,
            "Property Type": self.property_type,
            "Property ID": self.property_id,
            "Listing ID": self.listing_id,
            "Land Size": self.land_size,
            "Building Size": self.building_size,
            "EER": self.eer,
            "Contract Date": self.contract_date,
            "Listed Date": self.listed_date,
            "Last Updated": self.last_updated,
            "Days On Market": self.days_on_market,
            "Page Views": self.page_views,
            "Sale Status": self.sale_status,
            "Agent": self.agent,
            "Agents": self.agents,
            "Agency": self.agency,
            "Agent Photo": self.agent_photo,
            "Agency Logo": self.agency_logo,
            "Property Features": self.property_features,
            "Inspections": self.inspections,
            "Auction": self.auction,
            "Description": self.description,
            "Source Site": self.source_site,
            "Listing URL": self.listing_url,
            "Image URLs": ", ".join(self.image_urls),
            "Local Image Paths": ", ".join(self.local_image_paths),
            "Video URLs": ", ".join(self.video_urls),
            "Floorplan URLs": ", ".join(self.floorplan_urls),
            "Source Page": self.source_page,
        }
        row.update(self.dynamic_data)
        return row
