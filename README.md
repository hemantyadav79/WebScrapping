# WebArchiver — Enterprise Website Discovery, Archival, Analysis & Migration Platform

A production-grade, modular Python platform that crawls one or many websites,
archives every publicly accessible page and asset, builds a fully
offline-browsable clone, and produces a complete inventory, analytics and
migration report suite.

This is **not** a simple scraper. It is a scalable pipeline built on SOLID
principles with a swappable browser layer, concurrent downloading, duplicate
detection, resume/recovery, and enterprise logging.

---

## Features

- **Multi-site crawling** — process many websites in one run, each isolated so
  one failure never aborts the batch.
- **Discovery** — recursive, same-domain BFS link discovery with URL
  normalisation, depth/page limits, redirect-chain capture and status codes.
- **Content extraction** — title, meta description/keywords, canonical URL,
  `h1`–`h6`, language, Open Graph/Twitter cards, JSON-LD structured data,
  internal/external links.
- **Asset archival** — images (jpg/png/webp/svg/gif), videos (mp4/webm),
  documents (pdf/doc/docx/ppt/xls/txt/csv…), CSS, JS and fonts, organised into a
  strict folder tree.
- **Offline clone** — rewrites internal links, image/CSS/JS references and
  `srcset` so the archive browses without internet access.
- **Screenshots** — full-page captures (browser engine only).
- **Structured listings extraction** — parses property listings from the
  Next.js `__NEXT_DATA__` hydration JSON (CSS-selector DOM fallback) into
  `listings.csv` / `.xlsx` / `.json` with address, price, beds/baths/car,
  property type, agent/agency, listing URL and the downloaded photo path.
- **Data-quality gate** — a validator auto-fixes records and scores them 0–100
  (AU state/postcode checks, price range, type normalisation); records below
  `min_quality_score` are rejected and logged to `metadata/listings_quality.csv`.
- **Reports** — `pages`, `assets`, `crawl_report`, broken links, duplicate
  content (SHA-256), external-link inventory, plus `sitemap.xml`/`sitemap.json`
  and a cross-site `master_report.xlsx`. Exported to CSV, Excel and JSON.
- **Resilience** — async concurrency, token-bucket rate limiting, exponential
  backoff retries, timeouts, atomic checkpointing and `--resume`.
- **Swappable browser** — everything depends on the `RenderEngine` interface, so
  DrissionPage can be replaced with Playwright without touching crawl logic.
- **Proxy manager** — managed residential-proxy sessions with provider username
  formatting (BrightData, Oxylabs, Smartproxy, NetNut), sticky or rotating IPs,
  automatic failover/quarantine, and a `metadata/proxy_health.json` report.
- **Bot-block detection** — flags Kasada/Cloudflare/DataDome challenge pages
  (status codes, vendor markers, tiny bodies), retries with a fresh IP, and
  records blocked pages instead of silently archiving a broken shell.

---

## Requirements

- Python **3.12+**
- See `requirements.txt` (runtime) and `requirements-dev.txt` (tests/tooling).

Key libraries: DrissionPage, BeautifulSoup4, lxml, aiohttp, pandas, openpyxl.

---

## Installation

```bash
# from the project root
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt    # add -r requirements-dev.txt for tests

# optional editable install (exposes the `webarchiver` command)
pip install -e .
```

DrissionPage drives a real Chromium instance; install Chrome/Chromium if you
intend to use `render_engine: "drission"`. For static, server-rendered sites the
default `http` engine needs no browser.

---

## Usage

```bash
# run with a config file
python -m webarchiver.main --config config/sample_config.json

# or, if installed:
webarchiver --config config/sample_config.json

# resume an interrupted run from the last checkpoint
python -m webarchiver.main --config config/sample_config.json --resume
```

Exit codes: `0` success, `1` one or more sites had errors, `2` invalid config.

---

## Configuration

A single JSON file drives everything. `global` applies to all sites; each entry
in `websites` can override `render_engine` and toggle what to download.

```json
{
  "global": {
    "output_root": "website_backup",
    "concurrency": 8,
    "request_timeout": 30,
    "rate_limit_per_sec": 5,
    "max_retries": 3,
    "retry_backoff": 0.5,
    "user_agent": "WebArchiverBot/1.0 (+https://example.com/bot)",
    "respect_robots_txt": false,
    "render_engine": "http",
    "log_level": "INFO"
  },
  "websites": [
    {
      "name": "CorporateSite",
      "url": "https://example.com",
      "max_depth": 20,
      "max_pages": 5000,
      "take_screenshots": true,
      "download_images": true,
      "download_videos": true,
      "download_documents": true,
      "download_css": true,
      "download_js": true,
      "download_fonts": true,
      "rewrite_links": true,
      "render_engine": "http",
      "include_subdomains": false,
      "allowed_paths": [],
      "excluded_paths": ["/logout", "/admin"],
      "export_csv": true,
      "export_excel": true,
      "export_json": true
    }
  ]
}
```

| Field | Meaning |
|---|---|
| `render_engine` | `http` (aiohttp, fast, static) or `drission` (Chromium, JS) |
| `max_depth` / `max_pages` | crawl boundaries |
| `include_subdomains` | also crawl `*.domain` |
| `allowed_paths` / `excluded_paths` | path-prefix allow/deny lists |
| `download_*` | per asset-class toggles |
| `rewrite_links` | build the offline clone |
| `extract_listings` | parse property cards into a structured listings table |
| `listing_source` | `auto`, `realestate`, or `generic` extractor selection |
| `headless` / `page_load_wait` | run Chromium visibly and wait N s for JS/bot checks to clear (global) |
| `proxy` | direct proxy override, e.g. `http://user:pass@host:port` (global) |
| `proxy_provider` | `none`/`brightdata`/`oxylabs`/`smartproxy`/`netnut`/`custom` (global) |
| `proxy_host` / `proxy_port` / `proxy_username` / `proxy_password` | managed-proxy credentials (global) |
| `proxy_session_type` | `sticky` (reuse IP for TTL) or `rotating` (new IP/request) (global) |
| `block_detection` / `block_max_retries` | detect bot-challenge pages and retry with a fresh IP (global) |
| `validate_before_save` / `auto_fix_data` / `min_quality_score` | data-quality gate for extracted listings (global) |
| `export_*` | which report formats to emit |

---

## Output layout

```
website_backup/
└── CorporateSite/
    ├── html/                 # one .html per page
    ├── images/{jpg,png,webp,svg,gif,other}/
    ├── videos/  pdf/  documents/  css/  js/  fonts/
    ├── screenshots/
    ├── logs/                 # crawl.log, download.log, error.log
    ├── metadata/             # broken_links, duplicates, external_links, inventory.json
    ├── sitemap.xml  sitemap.json
    ├── pages.csv  pages.xlsx
    ├── assets.csv  assets.xlsx
    ├── crawl_report.csv  crawl_report.xlsx
    ├── listings.csv  listings.xlsx  listings.json   # when extract_listings=true
    └── crawl_state.json      # resume checkpoint
master_report.xlsx            # aggregate across all sites
```

---

## Architecture

The end-to-end pipeline flow (and how it maps to the PropFlow specification) is
documented in [`docs/FLOW.md`](docs/FLOW.md).

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the full write-up and the
Mermaid diagrams in [`docs/diagrams/`](docs/diagrams) (architecture, class,
sequence).

```
config  ── typed models + JSON validation
core    ── url normalisation, safe naming, hashing, logging, rate limiting
fetch   ── RenderEngine interface + HTTP (aiohttp) & DrissionPage engines
crawl   ── frontier, robots, BFS crawler
extract ── page parser, asset classifier, domain records
download ── concurrent asset downloader with dedupe
archive ── workspace/storage + offline link rewriter
screenshot ── full-page capture service
report  ── exporters, reporters, sitemap, master report
state   ── crawl_state checkpoint (resume)
pipeline / app / main ── orchestration + CLI
```

Design principles: each module has a single responsibility (SRP); the crawler,
downloader and reporters depend on abstractions, not concretions (DIP); the
engine is open for extension via new `RenderEngine` subclasses (OCP).

---

## Testing

```bash
pip install -r requirements-dev.txt
pytest                 # unit + integration
pytest --cov=webarchiver
```

The integration suite (`tests/integration/`) runs the **entire pipeline**
against an in-memory stub site (`tests/stubs.py`) — no network or browser
required — verifying crawl, dedupe, broken-link capture, screenshots, offline
rewrite, the full report suite and resume.

Lint / format / type-check:

```bash
ruff check . && black --check . && mypy webarchiver
```

---

## Extending to Playwright

Implement `webarchiver.fetch.engine.RenderEngine` with a Playwright backend and
register it in `webarchiver.fetch.factory.build_engine` under a new
`RenderEngineType`. No crawl, download or report code changes.

## Migration notes

The `assets.csv`/`pages.csv` inventories plus `metadata/inventory.json` capture
URL → local-path mappings, content hashes and the reference graph — the inputs a
website-to-website or website-to-SharePoint migration needs for metadata mapping
and link remapping.

## License

MIT.


## Latest capabilities

- **allhomes full sold history** via the site's GraphQL persisted query (`pagination_graphql: true`, `graphql_hash`) — cache-proof, pulls every page.
- **Property detail scraping** (`fetch_details: true`, `fetch_details_limit`) — visits each property page and writes `details.json`: full photo gallery, key details, block information + unimproved-value history, nearby sales, risks & heritage, what's nearby, suburb statistics, sale history.
- **realestate inside details** come free from the search store (full description, agents + phones, building size, inspections, all photos, floorplans).
- **Bot-block detection** and **managed residential proxy** support.
- **Auto-built website** (`build_website: true`, default on): every scrape folder gets a self-contained, dynamic site (`index.html` / `app.js` / `style.css` / `serve.bat`) that reads its own `listings.json` + `details.json` — search, dynamic filters (status/type/suburb), sort, live count, and a rich detail modal with gallery. Open `serve.bat`.

See `Property_Scraper_User_Guide.docx` for the full guide.
