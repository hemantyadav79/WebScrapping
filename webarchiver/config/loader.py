"""Load and validate the JSON configuration file into typed dataclasses."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from webarchiver.config.models import (
    AppConfig,
    GlobalConfig,
    RenderEngineType,
    WebsiteConfig,
)
from webarchiver.core.exceptions import ConfigError, ValidationError
from webarchiver.core.url_utils import is_valid_http_url


def _coerce_engine(value: Any, default: RenderEngineType | None) -> RenderEngineType | None:
    if value is None:
        return default
    try:
        return RenderEngineType(str(value).lower())
    except ValueError as exc:
        valid = ", ".join(e.value for e in RenderEngineType)
        raise ValidationError(f"Invalid render_engine '{value}'. Valid: {valid}") from exc


def _build_global(raw: dict[str, Any]) -> GlobalConfig:
    g = GlobalConfig()
    data = {
        "output_root": raw.get("output_root", g.output_root),
        "concurrency": int(raw.get("concurrency", g.concurrency)),
        "request_timeout": int(raw.get("request_timeout", g.request_timeout)),
        "rate_limit_per_sec": float(raw.get("rate_limit_per_sec", g.rate_limit_per_sec)),
        "max_retries": int(raw.get("max_retries", g.max_retries)),
        "retry_backoff": float(raw.get("retry_backoff", g.retry_backoff)),
        "user_agent": str(raw.get("user_agent", g.user_agent)),
        "proxy": str(raw.get("proxy", g.proxy) or ""),
        "proxy_provider": str(raw.get("proxy_provider", g.proxy_provider)).lower(),
        "proxy_host": str(raw.get("proxy_host", g.proxy_host)),
        "proxy_port": int(raw.get("proxy_port", g.proxy_port) or 0),
        "proxy_username": str(raw.get("proxy_username", g.proxy_username)),
        "proxy_password": str(raw.get("proxy_password", g.proxy_password)),
        "proxy_country": str(raw.get("proxy_country", g.proxy_country)).lower(),
        "proxy_session_type": str(raw.get("proxy_session_type", g.proxy_session_type)).lower(),
        "proxy_session_ttl_mins": int(raw.get("proxy_session_ttl_mins", g.proxy_session_ttl_mins)),
        "proxy_rotate_on_fail": bool(raw.get("proxy_rotate_on_fail", g.proxy_rotate_on_fail)),
        "proxy_max_failures": int(raw.get("proxy_max_failures", g.proxy_max_failures)),
        "block_detection": bool(raw.get("block_detection", g.block_detection)),
        "block_min_size_bytes": int(raw.get("block_min_size_bytes", g.block_min_size_bytes)),
        "block_max_retries": int(raw.get("block_max_retries", g.block_max_retries)),
        "validate_before_save": bool(raw.get("validate_before_save", g.validate_before_save)),
        "auto_fix_data": bool(raw.get("auto_fix_data", g.auto_fix_data)),
        "min_quality_score": int(raw.get("min_quality_score", g.min_quality_score)),
        "use_ollama": bool(raw.get("use_ollama", g.use_ollama)),
        "ollama_host": str(raw.get("ollama_host", g.ollama_host)).rstrip("/"),
        "ollama_model": str(raw.get("ollama_model", g.ollama_model)),
        "ollama_timeout": int(raw.get("ollama_timeout", g.ollama_timeout)),
        "respect_robots_txt": bool(raw.get("respect_robots_txt", g.respect_robots_txt)),
        "render_engine": _coerce_engine(raw.get("render_engine"), g.render_engine),
        "headless": bool(raw.get("headless", g.headless)),
        "page_load_wait": float(raw.get("page_load_wait", g.page_load_wait)),
        "override_user_agent": bool(raw.get("override_user_agent", g.override_user_agent)),
        "build_website": bool(raw.get("build_website", g.build_website)),
        "stealth": bool(raw.get("stealth", g.stealth)),
        "chrome_user_data_dir": str(raw.get("chrome_user_data_dir", g.chrome_user_data_dir)).strip(),
        "chrome_debugger_address": str(raw.get("chrome_debugger_address", g.chrome_debugger_address)).strip(),
        "detail_delay_sec": float(raw.get("detail_delay_sec", g.detail_delay_sec)),
        "chrome_fallback_cloak": bool(raw.get("chrome_fallback_cloak", g.chrome_fallback_cloak)),
        "chrome_cloak_path": str(raw.get("chrome_cloak_path", g.chrome_cloak_path)).strip(),
        "supabase_url": str(raw.get("supabase_url", g.supabase_url)).strip(),
        "supabase_key": str(raw.get("supabase_key", g.supabase_key)).strip(),
        "log_level": str(raw.get("log_level", g.log_level)).upper(),
    }
    if data["concurrency"] < 1:
        raise ValidationError("global.concurrency must be >= 1")
    if data["request_timeout"] < 1:
        raise ValidationError("global.request_timeout must be >= 1")
    if data["rate_limit_per_sec"] <= 0:
        raise ValidationError("global.rate_limit_per_sec must be > 0")
    return GlobalConfig(**data)


def _build_website(raw: dict[str, Any], idx: int) -> WebsiteConfig:
    if "name" not in raw or not str(raw["name"]).strip():
        raise ValidationError(f"websites[{idx}].name is required")
    if "url" not in raw:
        raise ValidationError(f"websites[{idx}].url is required")
    url = str(raw["url"]).strip()
    if not is_valid_http_url(url):
        raise ValidationError(f"websites[{idx}].url is not a valid http(s) URL: {url!r}")

    def b(key: str, default: bool) -> bool:
        return bool(raw.get(key, default))

    site = WebsiteConfig(
        name=str(raw["name"]).strip(),
        url=url,
        max_depth=int(raw.get("max_depth", 10)),
        max_pages=int(raw.get("max_pages", 10_000)),
        take_screenshots=b("take_screenshots", False),
        download_images=b("download_images", True),
        download_videos=b("download_videos", True),
        download_documents=b("download_documents", True),
        download_css=b("download_css", True),
        download_js=b("download_js", True),
        download_fonts=b("download_fonts", True),
        rewrite_links=b("rewrite_links", True),
        mode=str(raw.get("mode", "archive")).lower(),
        follow_pagination=b("follow_pagination", True),
        pagination_param=str(raw.get("pagination_param", "")).strip(),
        pagination_hash=b("pagination_hash", False),
        pagination_graphql=b("pagination_graphql", False),
        pagination_search_api=b("pagination_search_api", False),
        fetch_details=b("fetch_details", False),
        fetch_details_limit=int(raw.get("fetch_details_limit", 0)),
        graphql_hash=str(raw.get("graphql_hash", "")).strip(),
        graphql_page_size=int(raw.get("graphql_page_size", 10)),
        extract_listings=b("extract_listings", False),
        listing_source=str(raw.get("listing_source", "auto")).lower(),
        render_engine=_coerce_engine(raw.get("render_engine"), None),
        include_subdomains=b("include_subdomains", False),
        allowed_paths=list(raw.get("allowed_paths", []) or []),
        excluded_paths=list(raw.get("excluded_paths", []) or []),
        export_csv=b("export_csv", True),
        export_excel=b("export_excel", True),
        export_json=b("export_json", True),
    )
    if site.max_depth < 0:
        raise ValidationError(f"websites[{idx}].max_depth must be >= 0")
    if site.max_pages < 1:
        raise ValidationError(f"websites[{idx}].max_pages must be >= 1")
    if site.mode not in ("archive", "listings", "both"):
        raise ValidationError(f"websites[{idx}].mode must be archive|listings|both")
    return site


def load_config(path: str | Path) -> AppConfig:
    """Load, parse and validate the configuration file at ``path``.

    Raises:
        ConfigError: if the file is missing or not valid JSON.
        ValidationError: if any field fails validation.
    """
    cfg_path = Path(path)
    if not cfg_path.is_file():
        raise ConfigError(f"Configuration file not found: {cfg_path}")
    try:
        raw = json.loads(cfg_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Configuration file is not valid JSON: {exc}") from exc

    if not isinstance(raw, dict):
        raise ConfigError("Top-level configuration must be a JSON object")

    websites_raw = raw.get("websites")
    if not isinstance(websites_raw, list) or not websites_raw:
        raise ValidationError("Configuration must contain a non-empty 'websites' array")

    global_cfg = _build_global(raw.get("global", {}) or {})
    websites = [_build_website(w, i) for i, w in enumerate(websites_raw)]

    names = [w.name for w in websites]
    if len(names) != len(set(names)):
        raise ValidationError("Website 'name' values must be unique")

    return AppConfig(global_config=global_cfg, websites=websites)
