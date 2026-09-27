"""Command-line entry point.

Usage:
    python -m webarchiver.main --config config/sample_config.json
    python -m webarchiver.main --config config/sample_config.json --resume
"""
from __future__ import annotations

import argparse
import sys

from webarchiver.app import Application
from webarchiver.config.loader import load_config
from webarchiver.core.exceptions import ConfigError, ValidationError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="webarchiver",
        description="Enterprise Website Discovery, Archival, Analysis & Migration Platform",
    )
    parser.add_argument(
        "-c", "--config", default="config/sample_config.json",
        help="Path to the JSON configuration file (default: config/sample_config.json)",
    )
    parser.add_argument(
        "--resume", action="store_true",
        help="Resume from the last saved crawl_state.json for each site",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = load_config(args.config)
    except (ConfigError, ValidationError) as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    summaries = Application(config, resume=args.resume).run()

    print("\n=== Run summary ===")
    for s in summaries:
        status = "OK" if not s.errors else f"ERROR ({'; '.join(s.errors)})"
        print(
            f"- {s.name}: {s.pages_downloaded}/{s.pages_discovered} pages, "
            f"{s.images_downloaded + s.videos_downloaded + s.documents_downloaded} media assets, "
            f"{s.total_size_human}, {s.crawl_duration_sec:.1f}s [{status}]"
        )
    return 0 if all(not s.errors for s in summaries) else 1


if __name__ == "__main__":
    raise SystemExit(main())
