"""Typed configuration models (dataclasses).

These dataclasses are the single source of truth for runtime options. They are
populated by :mod:`webarchiver.config.loader` from a JSON file and validated
before any crawling begins.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class RenderEngineType(str, Enum):
    """Supported page-fetch strategies."""

    HTTP = "http"          # Fast static fetch via aiohttp.
    DRISSION = "drission"  # Full Chromium rendering via DrissionPage.


@dataclass(slots=True)
class GlobalConfig:
    """Options shared by every website in a run."""

    output_root: str = "website_backup"
    concurrency: int = 8
    request_timeout: int = 30
    rate_limit_per_sec: float = 5.0
    max_retries: int = 3
    retry_backoff: float = 0.5
    user_agent: str = "WebArchiverBot/1.0 (+https://example.com/bot)"
    proxy: str = ""  # direct override, e.g. "http://user:pass@host:port"; takes precedence
    # --- managed residential-proxy settings (used when 'proxy' is empty) ---
    proxy_provider: str = "none"  # none|brightdata|oxylabs|smartproxy|netnut|custom
    proxy_host: str = ""
    proxy_port: int = 0
    proxy_username: str = ""
    proxy_password: str = ""
    proxy_country: str = "au"
    proxy_session_type: str = "sticky"  # sticky|rotating
    proxy_session_ttl_mins: int = 15
    proxy_rotate_on_fail: bool = True
    proxy_max_failures: int = 3
    # --- bot-block detection ---
    block_detection: bool = True
    block_min_size_bytes: int = 2048  # bodies smaller than this are treated as suspicious
    block_max_retries: int = 2        # re-fetch attempts when a challenge page is detected
    # --- data validation / quality gate ---
    validate_before_save: bool = True
    auto_fix_data: bool = True
    min_quality_score: int = 30
    # --- Ollama LLM (optional; auto-falls back to heuristics if unreachable) ---
    use_ollama: bool = True
    ollama_host: str = "http://localhost:11434"
    ollama_model: str = "llama3.2"
    ollama_timeout: int = 20
    respect_robots_txt: bool = False
    render_engine: RenderEngineType = RenderEngineType.HTTP
    headless: bool = False  # run Chromium visibly; harder for bot-detection to flag
    page_load_wait: float = 8.0  # seconds to wait after navigation (lets Kasada clear)
    override_user_agent: bool = False  # if False, keep real Chrome UA (better for bot evasion)
    build_website: bool = True   # copy the runnable website into each site folder after a run
    # --- anti-block / stealth (DrissionPage) ---
    stealth: bool = True               # drop the automation flag so the browser looks human
    chrome_user_data_dir: str = ""     # use a persistent real Chrome profile (cookies persist)
    chrome_debugger_address: str = ""  # attach to an already-running Chrome, e.g. "127.0.0.1:9222"
    detail_delay_sec: float = 0.0      # base delay between detail-page fetches (jittered) to look human
    chrome_fallback_cloak: bool = False  # if DrissionPage fails, retry with CloakBrowser
    chrome_cloak_path: str = ""          # path to the CloakBrowser (stealth Chromium) executable
    # --- Supabase upload (optional; leave blank to skip) ---
    supabase_url: str = ""               # e.g. https://xxxx.supabase.co
    supabase_key: str = ""               # service-role key (preferred) or anon key
    log_level: str = "INFO"


@dataclass(slots=True)
class WebsiteConfig:
    """Per-website crawl configuration."""

    name: str
    url: str
    max_depth: int = 10
    max_pages: int = 10_000
    take_screenshots: bool = False
    download_images: bool = True
    download_videos: bool = True
    download_documents: bool = True
    download_css: bool = True
    download_js: bool = True
    download_fonts: bool = True
    rewrite_links: bool = True
    follow_pagination: bool = True  # traverse page 2..N links at the same depth
    mode: str = "archive"            # "archive" | "listings" | "both"
    follow_pagination: bool = True   # follow list-2, list-3 ... pagination links
    pagination_param: str = ""       # if set, paginate via ?<param>=N (e.g. "page")
    pagination_hash: bool = False    # SPA paginates via URL hash #N (allhomes sales history)
    pagination_graphql: bool = False # allhomes sold: page via the GraphQL persisted query (best)
    pagination_search_api: bool = False  # allhomes for-sale: page via /wsvc/search (self-configured from page 1)
    fetch_details: bool = False      # visit each property page for full detail + gallery
    fetch_details_limit: int = 0     # 0 = all; >0 caps detail visits (for testing)
    graphql_hash: str = ""           # override persisted-query hash if the site rotates it
    graphql_page_size: int = 10
    extract_listings: bool = False  # parse structured property listings from result pages
    listing_source: str = "auto"     # "auto" | "realestate" | "generic" | "allhomes"
    render_engine: RenderEngineType | None = None
    include_subdomains: bool = False
    allowed_paths: list[str] = field(default_factory=list)
    excluded_paths: list[str] = field(default_factory=list)
    export_csv: bool = True
    export_excel: bool = True
    export_json: bool = True


@dataclass(slots=True)
class AppConfig:
    """Top-level configuration aggregate."""

    global_config: GlobalConfig
    websites: list[WebsiteConfig]

    def resolved_engine(self, site: WebsiteConfig) -> RenderEngineType:
        """Return the effective render engine for ``site`` (site overrides global)."""
        return site.render_engine or self.global_config.render_engine
