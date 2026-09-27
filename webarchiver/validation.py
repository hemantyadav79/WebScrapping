"""
Real-data validation for the Belconnen scraper.

Runs automatically at the end of every scraper execution and:
  * validates every scraped record with offline checks (schema, per-field rules,
    content integrity, business rules, extra fields),
  * runs dataset-level checks (duplicates, record count, completeness, consistency),
  * compares a SAMPLE of records against the LIVE original source by re-fetching
    each property through the scraper's own engine (the only automated path that
    passes Akamai) and diffing field-by-field,
  * writes a NEW timestamped report (JSON + HTML + optional CSV) per run into
    validation-reports/, never overwriting previous reports.

Full source comparison of all records is supported (source_sample = 0 = ALL) but the
default is a sample, because re-fetching every record doubles load on a bot-protected
site. Offline + dataset checks always cover 100% of records.

Per-field rules (N/A is an accepted value everywhere):
  Bedrooms / Bathrooms / Parking .......... 0 or any integer
  EER ..................................... integer or float
  Building Size ........................... blank or an integer
  Price / Sold Price ...................... numeric or string, but NOT 0
  Status / Sold Date / Listing Type ....... N/A or string
  Listing ID .............................. must be numeric
  Last Updated / Listed Date / Agent Name / Agent Phone / Agency Phone /
    Image URL / Local Image Path / Property History / Features / Description
                                            N/A or string (no format check)
  Views / Page Views ...................... 0 or any numeric
  Image Count ............................. 0 or any numeric
"""
from __future__ import annotations

import asyncio
import csv
import html
import json
import random
import re
from dataclasses import dataclass, field as dc_field, asdict
from datetime import datetime, timezone
from pathlib import Path

PASS, FAIL, WARN = "PASS", "FAIL", "WARNING"

# Canonical record shape (display keys produced by _detail_record)
CANONICAL = [
    "id", "Address", "Street", "Suburb", "State", "Postcode", "Property Type",
    "Bedrooms", "Bathrooms", "Parking", "EER", "Building Size", "Price / Sold Price",
    "Status", "Sold Date", "Listing ID", "Viewed (page views)", "Last Updated",
    "Listed Date", "Description", "Features", "Key Details", "Other / Block Details",
    "What's Nearby", "Property History", "Agent Name", "Agent Phone", "Agency",
    "Agency Phone", "Source Locality", "Source Type", "Image Count", "Image URLs",
    "Local Image Paths", "Listing URL",
]
# structurally required (N/A / missing here is a failure)
REQUIRED = ["id", "Address", "Suburb", "Listing URL"]
# 0 or any integer
INT_FIELDS = ("Bedrooms", "Bathrooms", "Parking")
# 0 or any numeric (integer or float)
NUM_FIELDS = ("EER", "Viewed (page views)", "Image Count")
# fields compared against the live source (must be present on a detail page)
SOURCE_COMPARE = ["Address", "Bedrooms", "Bathrooms", "Parking", "Price / Sold Price"]

_URL_RE = re.compile(r"^https?://[^\s]+$", re.I)
_HTML_RE = re.compile(r"<[a-z/][^>]*>", re.I)
_INT_RE = re.compile(r"-?\d+")               # whole-string integer (use .fullmatch)
_NUM_RE = re.compile(r"-?\d+(\.\d+)?")        # whole-string integer or float
_ZERO_RE = re.compile(r"^\$?0+(\.0+)?$")      # 0, $0, 0.00, $0.00
_COMPLETENESS_MIN = 0.90                       # warn if a core field is <90% populated


def _blank(v) -> bool:
    s = "" if v is None else str(v).strip()
    return s == "" or s.upper() == "N/A"


def _norm(v) -> str:
    return re.sub(r"\s+", " ", str(v if v is not None else "")).strip().lower()


def _digits(v) -> str:
    m = re.search(r"\d+", str(v if v is not None else ""))
    return m.group(0) if m else ""


@dataclass
class Result:
    severity: str
    rule: str
    record_id: str = ""
    field: str = ""
    expected: str = ""
    actual: str = ""
    description: str = ""
    timestamp: str = dc_field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class ScrapeValidator:
    def __init__(self, scraper_name="belconnen", out_dir=".", *, report_dir=None,
                 source_sample=50, formats=("json", "html", "csv"), expected_count=None):
        self.scraper_name = scraper_name
        self.out = Path(out_dir)
        self.report_dir = Path(report_dir) if report_dir else (self.out / "validation-reports")
        self.source_sample = int(source_sample)   # 0 = compare ALL records
        self.formats = tuple(formats)
        self.expected_count = expected_count
        self.results: list[Result] = []
        self.records_validated = 0
        self.source_checked = 0

    def _rid(self, rec) -> str:
        return str(rec.get("id") or rec.get("Listing ID") or rec.get("Listing URL") or "?")

    # ---------------- per-record offline checks ----------------
    def validate_record(self, rec: dict) -> None:
        rid = self._rid(rec)
        self.records_validated += 1
        _start = len(self.results)
        R = self.results.append

        # required fields present + non-blank
        for f in REQUIRED:
            if f not in rec:
                R(Result(FAIL, "required_field_missing", rid, f, "present", "absent",
                         f"Required field '{f}' is missing from the record."))
            elif _blank(rec.get(f)):
                R(Result(FAIL, "required_field_empty", rid, f, "non-empty", "N/A/empty",
                         f"Required field '{f}' is empty."))

        # schema: every canonical key should exist (defaults fill blanks)
        for f in CANONICAL:
            if f not in rec:
                R(Result(FAIL, "field_missing", rid, f, "present", "absent",
                         f"Canonical field '{f}' is missing (defaulting failed)."))
            elif rec.get(f) is None or (isinstance(rec.get(f), str) and rec.get(f) == ""):
                R(Result(WARN, "null_field", rid, f, "'N/A' or 0", "null/empty",
                         f"Field '{f}' is null/empty instead of a default value."))

        # unexpected / extra fields
        for f in rec.keys():
            if f not in CANONICAL:
                R(Result(WARN, "unexpected_field", rid, f, "(not in schema)", str(rec.get(f))[:40],
                         f"Unexpected extra field '{f}'."))

        # Bedrooms / Bathrooms / Parking: 0 or any integer (N/A allowed)
        for f in INT_FIELDS:
            v = rec.get(f)
            if _blank(v):
                continue
            if not _INT_RE.fullmatch(str(v).strip()):
                R(Result(FAIL, "integer_value", rid, f, "integer (0 or more)", str(v)[:40],
                         f"Field '{f}' must be an integer."))

        # EER / Views / Image Count: 0 or any numeric (integer or float; N/A allowed)
        for f in NUM_FIELDS:
            v = rec.get(f)
            if _blank(v):
                continue
            if not _NUM_RE.fullmatch(str(v).strip()):
                R(Result(FAIL, "numeric_value", rid, f, "number (0 or more)", str(v)[:40],
                         f"Field '{f}' must be a numeric value."))

        # Building Size: blank or an integer
        bs = rec.get("Building Size")
        if not _blank(bs) and not _INT_RE.fullmatch(str(bs).strip()):
            R(Result(WARN, "building_size_value", rid, "Building Size", "blank or integer", str(bs)[:40],
                     "Building Size should be blank or an integer."))

        # Price / Sold Price: numeric or string, but must NOT be 0
        price = str(rec.get("Price / Sold Price") or "").strip()
        if price and not _blank(price) and _ZERO_RE.match(price.replace(",", "")):
            R(Result(FAIL, "price_zero", rid, "Price / Sold Price", "not 0", price[:40],
                     "Price / Sold Price must not be 0."))

        # Listing ID: a numeric value or N/A
        lid = str(rec.get("Listing ID") or "").strip()
        if not _blank(lid) and not _INT_RE.fullmatch(lid):
            R(Result(FAIL, "listing_id_numeric", rid, "Listing ID", "number or N/A", lid[:40],
                     "Listing ID must be a numeric value or N/A."))

        # Listing URL must be a valid http(s) URL
        v = rec.get("Listing URL")
        if not _blank(v) and not _URL_RE.match(str(v).strip()):
            R(Result(FAIL, "url_format", rid, "Listing URL", "http(s) URL", str(v)[:60],
                     "Field 'Listing URL' is not a valid URL."))

        # content integrity: text fields should not carry raw HTML
        for f in ("Description", "Features", "Key Details", "What's Nearby", "Property History"):
            tv = rec.get(f)
            if tv and _HTML_RE.search(str(tv)):
                R(Result(WARN, "content_integrity_html", rid, f, "plain text", "contains HTML tags",
                         f"Field '{f}' still contains raw HTML."))

        # business rule: ACT postcode range
        pc = str(rec.get("Postcode") or "").strip()
        if pc and not _blank(pc) and not pc.startswith("26"):
            R(Result(WARN, "business_postcode", rid, "Postcode", "ACT 26xx", pc,
                     "Postcode is outside the expected ACT/Belconnen range."))

        # record-level verdict: a record with no FAIL is counted as a clean PASS
        if not any(r.severity == FAIL for r in self.results[_start:]):
            R(Result(PASS, "record_valid", rid, "", "", "",
                     "Record passed all offline validation checks."))

    # ---------------- dataset-level checks ----------------
    def validate_dataset(self, records: list[dict]) -> None:
        for key, label in (("id", "duplicate_id"), ("Listing URL", "duplicate_url")):
            seen, dupes = set(), set()
            for r in records:
                v = r.get(key)
                if v in seen:
                    dupes.add(v)
                seen.add(v)
            for v in dupes:
                self.results.append(Result(FAIL, label, str(v), key, "unique", "duplicate",
                                           f"Duplicate record for {key} = {v}."))

        if self.expected_count is not None:
            sev = PASS if len(records) == self.expected_count else WARN
            self.results.append(Result(sev, "record_count", "", "count", str(self.expected_count), str(len(records)),
                                       f"Scraped {len(records)} records vs expected {self.expected_count}."))

        n = max(1, len(records))
        for f in ("Address", "Suburb", "Price / Sold Price", "Status", "Property Type", "Sold Date"):
            filled = sum(1 for r in records if not _blank(r.get(f)))
            ratio = filled / n
            if ratio < _COMPLETENESS_MIN:
                self.results.append(Result(WARN, "completeness", "", f, f">= {int(_COMPLETENESS_MIN*100)}%",
                                           f"{ratio*100:.1f}%", f"Field '{f}' only {ratio*100:.1f}% populated."))

        for r in records:
            if not _blank(r.get("Address")) and _blank(r.get("Suburb")):
                self.results.append(Result(WARN, "consistency_suburb", self._rid(r), "Suburb", "present", "N/A",
                                           "Record has an address but no suburb."))

    # ---------------- live source comparison (sampled) ----------------
    async def compare_against_source(self, records, engine, parser) -> None:
        if engine is None or parser is None or not records:
            return
        pool = [r for r in records if str(r.get("Listing URL") or "").startswith("http")]
        if not pool:
            return
        sample = pool if self.source_sample <= 0 else random.sample(pool, min(self.source_sample, len(pool)))
        numeric_keys = set(INT_FIELDS) | set(NUM_FIELDS)
        for rec in sample:
            url = rec["Listing URL"]; rid = self._rid(rec)
            try:
                res = await engine.fetch(url)
                if not getattr(res, "ok", False):
                    self.results.append(Result(WARN, "source_unreachable", rid, "Listing URL", "reachable",
                                               f"HTTP {getattr(res,'status','?')}", "Could not re-fetch source page."))
                    continue
                src = parser(res.html, url)
                if not src:
                    self.results.append(Result(WARN, "source_unparsed", rid, "Listing URL", "parseable", "empty",
                                               "Source page re-fetched but produced no data."))
                    continue
            except Exception as exc:  # noqa: BLE001
                self.results.append(Result(WARN, "source_error", rid, "Listing URL", "reachable", str(exc)[:60],
                                           "Error re-fetching source page."))
                continue
            self.source_checked += 1
            for f in SOURCE_COMPARE:
                got = rec.get(f)
                exp = src.get(f) if isinstance(src, dict) else None
                if exp is None and isinstance(src, dict):
                    exp = src.get(f.lower()) or src.get(f.replace(" / ", "_").lower())
                if _blank(exp):
                    continue
                a, b = (_digits(got), _digits(exp)) if f in numeric_keys else (_norm(got), _norm(exp))
                if a == b or (b and b in a) or (a and a in b):
                    self.results.append(Result(PASS, "source_match", rid, f, str(exp)[:40], str(got)[:40],
                                               "Scraped value matches source."))
                else:
                    self.results.append(Result(FAIL, "source_mismatch", rid, f, str(exp)[:60], str(got)[:60],
                                               f"Scraped '{f}' does not match live source."))

    # ---------------- orchestration ----------------
    async def run(self, records, engine=None, parser=None) -> dict:
        for rec in records:
            self.validate_record(rec)
        self.validate_dataset(records)
        await self.compare_against_source(records, engine, parser)
        return self.write_reports(records)

    # ---------------- reporting ----------------
    def _summary(self, records) -> dict:
        passed = sum(1 for r in self.results if r.severity == PASS)
        failed = sum(1 for r in self.results if r.severity == FAIL)
        warned = sum(1 for r in self.results if r.severity == WARN)
        total = len(self.results)
        records_passed = sum(1 for r in self.results if r.rule == "record_valid")
        records_failed = max(0, self.records_validated - records_passed)
        # success rate reflects EVERY check outcome: both failures and warnings
        # (e.g. a completeness check below its threshold) reduce it, across offline,
        # dataset and live-source checks. 100% only when there are zero of each.
        decided = passed + failed + warned
        rate = (passed / decided * 100) if decided else 100.0
        by_rule = {}
        for r in self.results:
            by_rule.setdefault(r.rule, {"PASS": 0, "FAIL": 0, "WARNING": 0})[r.severity] += 1
        return {
            "execution_timestamp": datetime.now(timezone.utc).isoformat(),
            "scraper_name": self.scraper_name,
            "records_scraped": len(records),
            "records_validated": self.records_validated,
            "records_passed": records_passed,
            "records_failed": records_failed,
            "source_records_compared": self.source_checked,
            "checks_performed": total,
            "passed": passed,
            "failed": failed,
            "warnings": warned,
            "success_rate_pct": round(rate, 2),
            "summary_by_rule": by_rule,
        }

    def write_reports(self, records) -> dict:
        self.report_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        base = self.report_dir / f"validation-report-{stamp}"
        summary = self._summary(records)
        failures = [asdict(r) for r in self.results if r.severity == FAIL]
        warnings = [asdict(r) for r in self.results if r.severity == WARN]
        paths = {}

        if "json" in self.formats:
            p = base.with_suffix(".json")
            tmp = p.with_suffix(".json.tmp")
            tmp.write_text(json.dumps({"summary": summary, "failures": failures, "warnings": warnings},
                                      indent=2, ensure_ascii=False), encoding="utf-8")
            tmp.replace(p)
            paths["json"] = str(p)

        if "csv" in self.formats:
            p = base.with_suffix(".csv")
            with open(p, "w", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh)
                w.writerow(["severity", "rule", "record_id", "field", "expected", "actual", "description", "timestamp"])
                for r in self.results:
                    if r.severity in (FAIL, WARN):
                        w.writerow([r.severity, r.rule, r.record_id, r.field, r.expected, r.actual, r.description, r.timestamp])
            paths["csv"] = str(p)

        if "html" in self.formats:
            p = base.with_suffix(".html")
            p.write_text(self._html(summary, failures, warnings), encoding="utf-8")
            paths["html"] = str(p)

        return {"summary": summary, "paths": paths}

    def _html(self, summary, failures, warnings) -> str:
        e = html.escape
        rate = summary["success_rate_pct"]
        if summary["failed"] > 0:
            badge = ("#b3261e", "FAILED")
        elif summary["warnings"] > 0:
            badge = ("#c98a16", "PASSED WITH WARNINGS")
        else:
            badge = ("#16a34a", "PASSED")

        def rows(items):
            out = ""
            for r in items:
                out += ("<tr><td>{sev}</td><td>{rule}</td><td>{rid}</td><td>{field}</td>"
                        "<td>{exp}</td><td>{act}</td><td>{desc}</td></tr>").format(
                    sev=e(r["severity"]), rule=e(r["rule"]), rid=e(r["record_id"][:40]), field=e(r["field"]),
                    exp=e(str(r["expected"])[:60]), act=e(str(r["actual"])[:60]), desc=e(r["description"]))
            return out or '<tr><td colspan="7" style="text-align:center;color:#64748b">none</td></tr>'

        cards = "".join(
            f'<div class="c"><b>{e(str(v))}</b><span>{e(k)}</span></div>' for k, v in [
                ("records scraped", summary["records_scraped"]),
                ("records passed", summary["records_passed"]),
                ("records failed", summary["records_failed"]),
                ("source compared", summary["source_records_compared"]),
                ("checks", summary["checks_performed"]),
                ("failed", summary["failed"]),
                ("warnings", summary["warnings"]),
                ("success rate", f'{rate}%'),
            ])
        return f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<title>Validation report — {e(summary['scraper_name'])} — {e(summary['execution_timestamp'])}</title>
<style>
body{{font-family:'Segoe UI',Arial,sans-serif;color:#1e293b;background:#eef2f7;margin:0}}
.wrap{{max-width:1080px;margin:0 auto;padding:22px}}
header{{background:linear-gradient(120deg,#1f3a5f,#2e75b6);color:#fff;border-radius:14px;padding:20px 24px}}
header h1{{margin:0;font-size:22px}} header .m{{color:#cfe2f5;font-size:13px;margin-top:4px}}
.b{{display:inline-block;background:{badge[0]};color:#fff;font-weight:700;padding:5px 14px;border-radius:999px;margin-top:10px}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:12px;margin:18px 0}}
.c{{background:#fff;border:1px solid #e2e9f2;border-radius:12px;padding:14px;text-align:center}}
.c b{{display:block;font-size:22px;color:#1f3a5f}} .c span{{font-size:12px;color:#64748b}}
h2{{color:#1f3a5f;margin:22px 0 8px}}
table{{width:100%;border-collapse:collapse;font-size:13px;background:#fff;border:1px solid #e2e9f2;border-radius:10px;overflow:hidden}}
th{{background:#1f3a5f;color:#fff;text-align:left;padding:8px 10px}} td{{padding:7px 10px;border-top:1px solid #eef2f7;vertical-align:top}}
tr:nth-child(even) td{{background:#f7f9fc}}
</style></head><body><div class="wrap">
<header><h1>Data Validation Report</h1>
<div class="m">Scraper: {e(summary['scraper_name'])} &middot; {e(summary['execution_timestamp'])}</div>
<span class="b">{badge[1]} &middot; {rate}% success</span></header>
<div class="cards">{cards}</div>
<h2>Failures ({summary['failed']})</h2>
<table><tr><th>Severity</th><th>Rule</th><th>Record</th><th>Field</th><th>Expected (source)</th><th>Actual (scraped)</th><th>Description</th></tr>{rows(failures)}</table>
<h2>Warnings ({summary['warnings']})</h2>
<table><tr><th>Severity</th><th>Rule</th><th>Record</th><th>Field</th><th>Expected</th><th>Actual</th><th>Description</th></tr>{rows(warnings)}</table>
</div></body></html>"""


def _cli(argv=None):
    """Offline validation of an existing dataset (no source re-fetch)."""
    import argparse
    ap = argparse.ArgumentParser(description="Validate a scraped Belconnen dataset (offline).")
    ap.add_argument("--file", default="website_backup/AllhomesBelconnen/belconnen_details.json")
    ap.add_argument("--out", default="website_backup/AllhomesBelconnen")
    args = ap.parse_args(argv)
    records = json.loads(Path(args.file).read_text(encoding="utf-8"))
    v = ScrapeValidator(out_dir=args.out, source_sample=0)
    res = asyncio.run(v.run(records, engine=None, parser=None))
    s = res["summary"]
    print(f"records={s['records_scraped']} records_passed={s['records_passed']} "
          f"records_failed={s['records_failed']} checks={s['checks_performed']} "
          f"failed={s['failed']} warnings={s['warnings']} success={s['success_rate_pct']}%")
    print("reports:", res["paths"])
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
