# Pipeline Flow — alignment with the PropFlow specification

This platform's runtime flow mirrors the PropFlow scraper pipeline
(PropFlow Technical Documentation §2.2 and §6.1). The table maps each PropFlow
stage to the module that performs it here. The end-to-end flow diagram is in
[`diagrams/pipeline_flow.mmd`](diagrams/pipeline_flow.mmd).

| #   | PropFlow stage                                              | webarchiver implementation                                                                                                                  |
| --- | ----------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | API request received                                        | `main.py` / `Application.run()` — config-driven run trigger                                                                                 |
| 2   | ProxyManager initialises / rotates worker sessions          | `proxy/manager.py` — sticky/rotating sessions, provider username formatting, failover                                                       |
| 3   | Browser launched with stealth flags                         | `fetch/drission_engine.py` — DrissionPage Chromium, `--disable-blink-features=AutomationControlled`, `auto_port`, real UA, `page_load_wait` |
| 4   | Geography traversal → pagination                            | `crawl/crawler.py` + `crawl/frontier.py` — BFS over same-domain links scoped by `allowed_paths` (e.g. `/buy/in-canberra` pagination)        |
| 5   | Per-listing: `__NEXT_DATA__` hydration JSON, DOM fallback   | `listings/hydration.py` + `listings/extractor.py` — hydration parse first, `residential-card` DOM fallback                                  |
| 6   | Bot-detection check (Kasada/Cloudflare/DataDome)            | `crawl/blockdetect.py` — markers + status + tiny-body; retry/reload, proxy rotation                                                         |
| 7   | Raw data object assembled                                   | `listings/models.py::ListingRecord` — address, price, beds/baths/parking, type, agent/agency, image, URL                                    |
| 8   | DataValidator: quality score + auto-fix, reject < threshold | `listings/validator.py` — 0–100 score, AU state/postcode checks, auto-fix, `min_quality_score` gate                                         |
| 9   | Clean record persisted                                      | `listings/reporter.py` + `report/*` — `listings.csv/xlsx/json` (the file-based analog of PropFlow's Supabase upsert)                        |
| 10  | Real-time broadcast (Socket.io)                             | Not implemented — out of scope for an archival tool (see note below)                                                                        |
| 11  | Image URLs downloaded in parallel                           | `download/asset_downloader.py` — concurrent, hash-dedup, browser-cookie + Referer for protected CDNs                                        |

## Stage-by-stage flow

1. **Proxy session** — `ProxyManager.current()` yields a proxy URL (sticky or
   rotating); on a block it rotates / quarantines.
2. **Engine launch** — the DrissionPage engine starts a real Chromium with
   low-fingerprint flags and the configured proxy.
3. **Traversal** — the crawler seeds the search URL and follows pagination
   links that match `allowed_paths`, depth- and page-bounded.
4. **Per-page fetch** — navigate, wait `page_load_wait` seconds for client-side
   rendering / bot-check to settle, scroll to trigger lazy content.
5. **Block check** — `is_blocked()` inspects the page; a challenge triggers a
   reload/rotate retry, and a persistent block marks the page failed.
6. **Extraction** — `__NEXT_DATA__` hydration JSON is parsed into structured
   records; if absent, the DOM `residential-card` extractor runs.
7. **Validation** — each record is auto-fixed then scored 0–100; records below
   `min_quality_score`, or with hard errors (bad/placeholder address, invalid
   AU state/postcode), are rejected and logged to `metadata/listings_quality.csv`.
8. **Persistence** — accepted records are written to `listings.csv/xlsx/json`.
9. **Images** — referenced photos are downloaded concurrently (carrying the
   browser's cookies + Referer so protected CDNs serve them) and de-duplicated.
10. **Reporting** — page/asset/crawl/broken/duplicate/external reports, sitemaps,
    `proxy_health.json`, and a cross-site `master_report.xlsx`.

## What is intentionally not ported

PropFlow is a larger product. The following stages are a different application
than a website archival/extraction tool and are intentionally **not** included:

- **Supabase database** — we persist to CSV/XLSX/JSON files instead of a cloud DB.
- **Socket.io real-time dashboard** — no live web UI.
- **AI / LLM enrichment service** — no description rewriting or semantic search.
- **SSH / cPanel static-site deployment** — no publish step.

The _scraping and data-quality flow_ (stages 1–9, 11) is matched; the
serving/enrichment/deploy half of PropFlow is out of scope.

## Updated pipeline (current)

1. ProxyManager session -> launch real Chrome (DrissionPage, stealth) -> bot-block check/retry.
2. Pagination: realestate `list-1..82` URLs; allhomes sold via GraphQL persisted query (`page 1..N`).
3. Extract listings: realestate `window.ArgonautExchange`; allhomes GraphQL `historyForLocality` / `__NEXT_DATA__`.
4. (Optional) Detail crawl: visit each property page; allhomes detail from `window['__domain_group/APP_PROPS'].body.property` -> `details.json` (gallery, block info, nearby sales, risks, suburb stats, ...).
5. Validate + auto-fix; download photos.
6. Publish the self-contained website into the site folder (auto, `build_website`).
