"""One-click website scraper.

Usage (any of these):
    python scrape.py https://example.com
    python scrape.py                       # uses URL below, or prompts you
    double-click run.bat                   # Windows

Edit this constant to hard-code the site you want, then just run the script:
"""
URL = ""  # <--- paste your website link here for true one-click runs

import argparse
import sys
from urllib.parse import urlsplit

from webarchiver.app import Application
from webarchiver.config.models import AppConfig, GlobalConfig, RenderEngineType, WebsiteConfig
from webarchiver.core.url_utils import is_valid_http_url


def site_name_from(url: str) -> str:
    host = urlsplit(url).hostname or "site"
    return host.removeprefix("www.")


def build_config(args: argparse.Namespace) -> AppConfig:
    gc = GlobalConfig(
        output_root=args.output,
        concurrency=args.concurrency,
        rate_limit_per_sec=args.rate,
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
        ),
        render_engine=RenderEngineType.DRISSION if args.browser else RenderEngineType.HTTP,
        headless=False,
        use_ollama=not args.no_ollama,
        ollama_model=args.ollama_model,
    )
    site_name = args.name if args.name else site_name_from(args.url)
    site = WebsiteConfig(
        name=site_name,
        url=args.url,
        max_depth=args.depth,
        max_pages=args.max_pages,
        follow_pagination=True,
        take_screenshots=args.screenshots,
        listing_source="ollama" if getattr(args, "ollama_extractor", False) else "auto",
    )
    return AppConfig(global_config=gc, websites=[site])


def main() -> int:
    parser = argparse.ArgumentParser(description="Scrape an entire website in one click.")
    parser.add_argument("url", nargs="?", default=URL, help="Website URL to scrape")
    parser.add_argument("--depth", type=int, default=10, help="Max link depth (default 10)")
    parser.add_argument("--max-pages", type=int, default=10000, help="Max pages (default 10000)")
    parser.add_argument("--output", default="website_backup", help="Output folder")
    parser.add_argument("--name", help="Custom name for output folder (e.g., Canberra)")
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--rate", type=float, default=5.0, help="Requests per second")
    parser.add_argument("--browser", action="store_true",
                        help="Use the Chromium (DrissionPage) engine for JavaScript-heavy sites")
    parser.add_argument("--screenshots", action="store_true", help="Capture page screenshots")
    parser.add_argument("--no-ollama", action="store_true", help="Disable the Ollama LLM filter")
    parser.add_argument("--ollama-model", default="llama3.2:1b")
    parser.add_argument("--ollama-extractor", action="store_true", help="Use Ollama to dynamically extract property listing data from pages")
    parser.add_argument("--resume", action="store_true", help="Resume an interrupted crawl")
    args = parser.parse_args()

    if not args.url:
        args.url = input("Enter the website URL to scrape: ").strip()
    if args.url and "://" not in args.url:
        args.url = "https://" + args.url
    if not is_valid_http_url(args.url):
        print(f"Not a valid http(s) URL: {args.url!r}", file=sys.stderr)
        return 2

    config = build_config(args)
    summaries = Application(config, resume=args.resume).run()

    print("\n=== Run summary ===")
    failed = False
    for s in summaries:
        status = "OK" if not s.errors else f"ERROR ({'; '.join(s.errors)})"
        failed = failed or bool(s.errors)
        print(
            f"- {s.name}: {s.pages_downloaded}/{s.pages_discovered} pages, "
            f"{s.images_downloaded + s.videos_downloaded + s.documents_downloaded} media assets, "
            f"{s.total_size_human}, {s.crawl_duration_sec:.1f}s [{status}]"
        )
        print(f"  Saved to: {s.output_dir}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
