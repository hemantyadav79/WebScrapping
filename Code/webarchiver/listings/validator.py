"""DataValidator: quality scoring + auto-fix gate for listing records.

Mirrors PropFlow's validator (doc §3.6): every record is auto-fixed, then
scored 0–100 on field validity and completeness. Records below
``min_quality_score`` are rejected. Auto-fix runs *before* scoring so records
that would otherwise fail are rescued.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from webarchiver.listings.models import ListingRecord

_AU_STATES = {"NSW", "VIC", "QLD", "SA", "WA", "TAS", "NT", "ACT"}
_PRICE_KEYWORDS = ("auction", "poa", "contact", "offers", "eoi", "expression",
                   "guide", "negotiation", "withheld")
_PLACEHOLDER_ADDR = {"", "n/a", "address available on request", "address withheld"}
_TYPE_CANON = {
    "house": "House", "unit": "Unit", "apartment": "Apartment", "townhouse": "Townhouse",
    "villa": "Villa", "land": "Land", "acreage": "Acreage", "duplex": "Duplex",
    "studio": "Studio", "rural": "Rural", "block of units": "Block of Units",
}
_STATE_RE = re.compile(r"\b(NSW|VIC|QLD|SA|WA|TAS|NT|ACT)\b", re.I)
_POSTCODE_RE = re.compile(r"\b(\d{4})\b")
_DIGIT_RE = re.compile(r"\d")
_INT_RE = re.compile(r"\d+")


@dataclass(slots=True)
class ValidationResult:
    record: ListingRecord
    score: int = 0
    rejected: bool = False
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    fixes: list[str] = field(default_factory=list)
    state: str = ""
    postcode: str = ""


class DataValidator:
    """Scores and (optionally) auto-fixes listing records before export."""

    def __init__(self, *, min_quality_score: int = 30, auto_fix: bool = True) -> None:
        self._min_score = min_quality_score
        self._auto_fix = auto_fix

    def validate(self, rec: ListingRecord) -> ValidationResult:
        res = ValidationResult(record=rec)
        if self._auto_fix:
            self._autofix(rec, res)

        # Address
        if rec.address.strip().lower() in _PLACEHOLDER_ADDR or len(rec.address.strip()) <= 5:
            res.errors.append("address missing or placeholder")

        # State (parsed from address)
        m = _STATE_RE.search(rec.address)
        res.state = m.group(1).upper() if m else ""
        if res.state and res.state not in _AU_STATES:
            res.errors.append(f"invalid state code: {res.state}")
        elif not res.state:
            res.warnings.append("no AU state in address")

        # Postcode (parsed from address)
        pm = _POSTCODE_RE.search(rec.address)
        res.postcode = pm.group(1) if pm else ""
        if not res.postcode:
            res.warnings.append("no 4-digit postcode in address")

        # Price
        low_price = rec.price.lower()
        if not (_DIGIT_RE.search(rec.price) or any(k in low_price for k in _PRICE_KEYWORDS)):
            res.warnings.append("price has no digit or recognised keyword")
        numeric = self._price_numeric(rec.price)
        if numeric is not None and not (1_000 <= numeric <= 500_000_000):
            res.warnings.append(f"price out of range: {numeric}")

        res.score = self._score(rec, res)
        res.rejected = bool(res.errors) or res.score < self._min_score
        return res

    # ---- helpers ------------------------------------------------------
    def _autofix(self, rec: ListingRecord, res: ValidationResult) -> None:
        for attr in ("bedrooms", "bathrooms", "parking"):
            val = getattr(rec, attr)
            if val and not val.isdigit():
                m = _INT_RE.search(val)
                fixed = m.group(0) if m else ""
                if fixed != val:
                    setattr(rec, attr, fixed)
                    res.fixes.append(f"{attr} -> {fixed!r}")
        if rec.property_type:
            canon = _TYPE_CANON.get(rec.property_type.strip().lower())
            if canon and canon != rec.property_type:
                res.fixes.append(f"property_type -> {canon!r}")
                rec.property_type = canon
        rec.address = re.sub(r"\s+", " ", rec.address).strip()
        rec.price = re.sub(r"\s+", " ", rec.price).strip()

    @staticmethod
    def _price_numeric(price: str) -> int | None:
        digits = re.sub(r"[,\s]", "", price)
        m = re.search(r"\$?(\d{4,})", digits)
        return int(m.group(1)) if m else None

    @staticmethod
    def _bed_ok(value: str) -> bool:
        return value.isdigit() and 0 <= int(value) <= 20

    def _score(self, rec: ListingRecord, res: ValidationResult) -> int:
        # Weighted completeness/validity score out of 100.
        score = 0
        if rec.address and rec.address.strip().lower() not in _PLACEHOLDER_ADDR:
            score += 25
        if res.state in _AU_STATES:
            score += 15
        if res.postcode:
            score += 10
        if _DIGIT_RE.search(rec.price) or any(k in rec.price.lower() for k in _PRICE_KEYWORDS):
            score += 20
        if self._bed_ok(rec.bedrooms):
            score += 10
        if self._bed_ok(rec.bathrooms):
            score += 5
        if rec.property_type:
            score += 5
        if rec.image_urls:
            score += 5
        if rec.listing_url:
            score += 5
        return min(100, score)


def validate_all(
    records: list[ListingRecord], *, min_quality_score: int = 30, auto_fix: bool = True
) -> tuple[list[ListingRecord], list[ValidationResult]]:
    """Validate every record; return (accepted_records, all_results)."""
    validator = DataValidator(min_quality_score=min_quality_score, auto_fix=auto_fix)
    results = [validator.validate(r) for r in records]
    accepted = [r.record for r in results if not r.rejected]
    return accepted, results
