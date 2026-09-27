# Property Scraper & Website — Full Project Documentation

_Last updated: 17 June 2026_

This document explains the whole system end to end: what it does, how it works,
why each design decision was made, every data field it captures, how to run it,
and the full history of changes made while building it.

---

## 1. What this project does

It scrapes residential property data from two Australian portals and turns it
into clean datasets plus a ready-to-use website:

- **realestate.com.au** — Canberra Greater Region, *for sale* (~3,000 listings, 80 pages).
- **allhomes.com.au** — Canberra region, *for sale* search (~2,516 listings, 51 pages),
  and (optionally) suburb *sold* history.

For every property it produces structured records (CSV / Excel / JSON), downloads
the photos, optionally visits each property's own page for richer detail, then
builds a searchable website and a combined, de-duplicated cross-site dataset with
basic market analytics.

---

## 2. The core challenge: bot protection (Kasada)

Both sites sit behind **Kasada**, a bot-protection service. A plain HTTP request
(`requests`, `aiohttp`) is blocked because Kasada runs JavaScript in the browser
to generate a signed token and special cookies. Without a real browser executing
that JavaScript, the server returns a block page instead of data.

**Solution: DrissionPage.** It drives a real Chromium browser through the Chrome
DevTools Protocol (CDP). Chrome loads the page, runs Kasada's JavaScript, solves
the challenge, and earns the legitimate cookies — exactly like a human. We then
read the data straight out of the rendered page, and reuse the browser's cookies
to download images. CDP control is also stealthier than Selenium (no
`navigator.webdriver` fingerprint), so Kasada is less likely to flag it.

Config flag: `"render_engine": "drission"` per site (the default engine stays
`http` for any non-protected site).

---

## 3. Where the data lives on each site

We never scrape the visible HTML text where we can avoid it — we read the
structured data the page already embeds.

### realestate.com.au
- **Search results:** `window.ArgonautExchange` → `urqlClientCache` →
  `buySearch.results.exact.items[].listing`. Gives address, price, beds/baths/car,
  property type, agents, images, etc.
- **Detail page:** rendered into the DOM with stable `data-testid` markers —
  `PropertyDescription`, `top-property-features-section`, plus text patterns
  `Property ID: <n>` and `<n> page views`.

### allhomes.com.au
- **For-sale search:** `window['__domain_group/APP_PROPS']` → `body.initialSearchResults`
  (50 listings per page), with `body.totalPages` and `body.query`. Paginated by URL.
- **Sold history:** `__NEXT_DATA__` → `props.pageProps.page.locality.history.nodes`,
  fully paged via the site's GraphQL persisted query (`updateHistoryForLocality`).
- **Detail page:** `window['__domain_group/APP_PROPS']` → `body.property` — the
  richest source (gallery, key details, block info, nearby sales, agents w/ photos,
  inspections, auctions, page views, sale history, suburb stats).

---

## 4. How pagination works (per source)

Different sites paginate differently, so there are three strategies, selected by
config flags:

1. **realestate (ordered URL list)** — `follow_pagination: true`. We generate
   `list-1 … list-80` URLs and crawl them in order (no link-following detours).

2. **allhomes for-sale search (URL `&page=N`)** — `pagination_search_api: true`.
   **Page 1** uses the plain URL (`?region=canberra-act`, *no* `&page`). **Page 2
   onward** appends `&page=2`, `&page=3`, … up to `&page=51`. Each page is fetched
   as normal HTML and its 50 listings are read from `APP_PROPS.initialSearchResults`.
   (We originally tried the `/wsvc/search` JSON API, but its live response shape
   differs from the page data, so URL pagination — which matches the site's own
   pagination links — is used instead. It is more reliable.)

3. **allhomes sold history (GraphQL)** — `pagination_graphql: true` with
   `graphql_hash`. Pages are pulled one at a time via the persisted query, run
   *inside* the browser so Kasada cookies/headers apply.

---

## 5. Data fields captured

### Listing-level (every property, in `listings.json/csv/xlsx`)
Address, Suburb, Price, Bedrooms, Bathrooms, Parking, Property Type, Property ID,
Listing ID, Land Size, Building Size, EER, Contract Date, Listed Date,
Last Updated, Days On Market, Page Views, Sale Status, Agent, Agents,
Agency, Agent Photo, Agency Logo, Property Features, Inspections, Auction,
Description, Source Site, Listing URL, Image URLs, Local Image Paths,
Video URLs, Floorplan URLs.

### Detail-level (when `fetch_details: true`, in `details.json`)
Full image gallery, full description, key details, **block information** (+ unimproved
value history), **nearby sales** / sold comparables, **risks & heritage**,
**what's nearby**, **suburb statistics**, **sale/property history**, agents with
profile photos + phones, agency logo, **inspections**, **auction**, **page views**
("Viewed N times"), **listing ID**, **last updated**.

The detail visit *enriches* the matching listing row too, so `listings.json` also
gains page views, property features, agent photo, last-updated, etc.

> Verified on a live Canberra detail page (24 French Street, Hackett): listing ID,
> 1187 page views, 2 agents with photos, agency logo, 4 inspections, 1 auction,
> 10 key details, sale history, 31 images — all extracted correctly.

---

## 6. Images: parallel download, crash-safe

Images download **in a background task, in parallel with crawling** — they are not
left to a single phase at the end. As each page is scraped, its photos are queued
and a background downloader continuously drains the queue, saving files under
`images/jpg|png|webp/...`. Benefits:

- The crawl is never blocked waiting on downloads.
- A crash (e.g. power loss) keeps every image downloaded so far.
- `Local Image Paths` in `listings.json` are linked **incrementally** as images
  land (not just at the end), so the dataset always points at the files on disk.

Note: some allhomes gallery URLs use the `images.qa.allhomes.com.au` staging host,
which occasionally returns HTTP 500. Those few failures are harmless — the primary
photos download fine, and the website uses the local copies.

---

## 7. Resume / checkpoint (crash recovery)

The run checkpoints continuously so it can resume after a crash without losing work
or re-scraping:

- **`crawl_state.json`** — frontier, pages, downloaded assets, and `extra`
  (which pages / detail URLs are already done).
- **`_resume/listings_state.json`** and **`_resume/details.json`** — full snapshots
  of everything collected, rewritten at every checkpoint (atomic writes).

To continue after a crash, add `--resume`:

```
python -m webarchiver.main --config config/sample_config.json --resume
```

On resume it reloads the collected listings + details, skips the pages/properties
already finished, and continues. Pagers (search + GraphQL) run **one page at a
time** specifically so each page can be checkpointed and its images saved before
moving on.

---

## 8. Combined dataset + analytics

After every site finishes, a combine step runs automatically and writes a
`combined/` folder under the output root:

- **`combined.{json,csv,xlsx}`** — all *unique* properties across both sites.
- **`duplicates.{json,csv,xlsx}`** — every dropped duplicate (full data + a
  `Duplicate Of` column). Duplicates are matched on the **normalised street
  address**; the first occurrence is kept, later ones go here.
- **`analytics.{json,xlsx}`** — the metrics derivable from scraped data:
  Agent sales rankings, Market share by suburb, Average days on market,
  Most viewed properties, and a Suburb demand score.

The combine step scans **all** site folders present under the output root, so it
works whether you scrape both sites in one config or in separate runs.

> Note on the client's wider metric wishlist: vendor discounting, listing-to-sale
> conversion, lead-response, buyer-enquiry/search-volume trends, interstate
> migration and buyer demographics **cannot** be obtained by scraping these two
> sites — they require the portals' private dashboards, agent CRM data, or ABS/
> census sources.

---

## 9. The website

Every site folder gets a self-contained website (built at the end of that site's
run, controlled by `build_website: true`, on by default):

- `index.html`, `app.js`, `style.css`, `serve.bat`
- Reads that folder's own `listings.json` + `details.json`.
- Features: search bar, dynamic filters (status / type / suburb), sort, live count,
  hover effects, and a detail modal with photo gallery, agent photos, agency logo,
  property-feature chips, inspections, auction, page views, and all detail sections.

**To view:** open the site folder, double-click `serve.bat`, then go to
`http://localhost:8000`. (A local server is required because browsers block
`file://` JSON reads.)

The `combined/` folder currently holds data files only — there is no combined
website yet (can be added on request).

---

## 10. Configuration

Configs live in `config/`. Run with `-c/--config`.

### Files
- **`sample_config.json`** — both sites, run together.
- **`realestate_canberra.json`** — realestate only (for parallel runs).
- **`allhomes_canberra.json`** — allhomes only (for parallel runs).
- (Older test configs: `realestate_only.json`, `allhomes_sold_test.json`,
  `allhomes_detail_test.json`.)

### Key options
| Option | Meaning |
|---|---|
| `mode` | `listings` (data + photos) / `archive` (full site) / `both` |
| `listing_source` | `realestate` / `allhomes` / `auto` / `generic` |
| `url` | starting URL (point at another suburb/region on the same site) |
| `max_pages` | how many pages to scrape (realestate 80, allhomes 51) |
| `follow_pagination` | true for realestate (list-1..N) |
| `pagination_search_api` | true for allhomes for-sale (URL `&page=N`) |
| `pagination_graphql` + `graphql_hash` | for allhomes *sold* history |
| `fetch_details` / `fetch_details_limit` | visit each property page (0 = all) |
| `download_images` | save photos locally |
| `build_website` | publish the website into the folder (global, default true) |

### Global speed/behaviour
| Option | Notes |
|---|---|
| `use_ollama` | keep **false** — extractors are rule-based; Ollama only adds latency |
| `page_load_wait` | seconds to wait per page (3 is a good balance; raise to 4–5 if detail parses come back empty) |
| `concurrency`, `rate_limit_per_sec` | raise for faster image downloads (5–8 ceiling to avoid blocks) |

---

## 11. How to run

### One-shot (both sites, then combined)
```
python -m webarchiver.main --config config/sample_config.json
```

### Faster: two sites in parallel (different domains, safe)
```
# window 1
python -m webarchiver.main --config config/realestate_canberra.json --resume
# window 2
python -m webarchiver.main --config config/allhomes_canberra.json
```
Both write to different folders under `website_backup/`; the combined files are
correct once **both** have finished.

### Resume after a crash
Add `--resume` to the same command. To start completely fresh, delete the
`website_backup` folder first and run **without** `--resume`.

---

## 12. Output layout

```
website_backup/
  RealestateCanberra/
    listings.json / .csv / .xlsx     ← the property data
    details.json                     ← full per-property detail (if fetch_details)
    images/jpg|png|webp/...          ← downloaded photos
    index.html app.js style.css serve.bat   ← the website
    crawl_state.json, _resume/       ← checkpoint + resume snapshots
    logs/  (crawl.log, download.log, error.log)
    metadata/ (listings_quality.csv, proxy_health.json)
  AllhomesCanberra/
    ... same structure ...
  combined/
    combined.{json,csv,xlsx}
    duplicates.{json,csv,xlsx}
    analytics.{json,xlsx}
  master_report.json / .xlsx
```

---

## 13. Reading the logs

- **`crawl.log`** — pages scraped, page numbers, listing counts.
- **`download.log`** — image downloads (and any failures, e.g. `qa` 500s).
- **`error.log`** — warnings (blocked pages, empty pages, parse misses).

If a run looks "stuck downloading", check `listings.json`'s row count and the
`images/` folder — both should be growing while the background downloader runs.

---

## 14. Troubleshooting

- **Only ~50 allhomes listings** → pagination wasn't advancing. Fixed by URL
  pagination (`&page=N`). Resume and watch `crawl.log` step through pages.
- **`Local Image Paths` empty mid-run** → links incrementally now; if still empty
  while `images/` has files, it's a URL-matching issue — report it.
- **`detail parse empty` warnings** → `page_load_wait` too short; raise to 4–5.
- **Slow detail phase** → it visits every property page in a real browser; lower
  `page_load_wait`, cap with `fetch_details_limit`, or disable `fetch_details`.
- **Blocked pages** → the engine retries; consider a residential proxy and lower
  `rate_limit_per_sec`.

---

## 15. Project history (what we did, in order)

1. **Built the realestate scraper** using `window.ArgonautExchange`; got past
   Kasada with DrissionPage; fixed ordered pagination (list-1..82).
2. **Fixed image downloads** — the binary fetch was using a non-existent session
   method; rewrote it to use the browser's cookies + correct `Referer`.
3. **Added allhomes sold history** via the GraphQL persisted query (cache-proof,
   full pagination) after the URL/hash approaches were cached by the site.
4. **Added allhomes property-detail scraping** from `APP_PROPS.body.property`
   (gallery, block info, nearby sales, risks, suburb stats, sale history).
5. **Built the dynamic website** that auto-publishes into every scrape folder and
   renders from `listings.json` + `details.json`.
6. **Switched the Canberra target** to the allhomes for-sale search and the full
   realestate set; added a self-configuring pager.
7. **Added the new fields** you requested: page views, listing ID, last updated,
   inspections, auction, property features, agent photos, agency logo.
8. **Added combined + dedup outputs and analytics** across both sites.
9. **Analysed the client metric wishlist** — identified which 5 are derivable and
   which 6 are not obtainable by scraping.
10. **Made images download in parallel** (background task) and **made resume real**
    (checkpoint + reload), so a power loss no longer loses images or progress.
11. **Linked local image paths incrementally** so `listings.json` always points at
    the saved files.
12. **Replaced the allhomes for-sale API pager with URL pagination** (`&page=N`)
    after the live API returned a different shape — page 1 has no `&page`, page 2+
    do, matching the site exactly.
13. **Sped up the detail phase** (lower `page_load_wait`, no Excel rewrite on every
    checkpoint) and added split configs for parallel runs.

All parsers are verified against saved live pages (Canberra search page 1 & 2, and
a Canberra detail page).
