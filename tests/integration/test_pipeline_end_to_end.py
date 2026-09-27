"""End-to-end pipeline test using the in-memory stub engine.

Exercises crawl -> HTML persistence -> asset download (with dedupe) ->
screenshots -> offline link rewrite -> full report suite, with no network.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.stubs import stub_factory
from webarchiver.pipeline import WebsiteProcessor


@pytest.fixture
async def processed(app_config):
    site = app_config.websites[0]
    proc = WebsiteProcessor(app_config, site, engine_factory=stub_factory)
    summary = await proc.run()
    root = Path(app_config.global_config.output_root) / "DemoSite"
    return summary, root


async def test_pages_crawled_and_saved(processed):
    summary, root = processed
    # 5 reachable pages + 1 broken (/missing) discovered from /contact.
    assert summary.pages_downloaded == 5
    assert summary.pages_failed == 1
    assert (root / "html").is_dir()
    assert any((root / "html").glob("*.html"))


async def test_assets_downloaded_and_deduplicated(processed):
    summary, root = processed
    assert summary.css_downloaded == 1
    assert summary.js_downloaded == 1
    assert summary.documents_downloaded == 1
    assert summary.images_downloaded >= 1
    # logo.png and item.png share bytes -> exactly one duplicate detected.
    assert summary.duplicate_assets == 1
    assert (root / "pdf").is_dir() and any((root / "pdf").glob("*.pdf"))


async def test_reports_and_sitemaps_exist(processed):
    _, root = processed
    for name in ("pages.csv", "pages.xlsx", "assets.csv", "assets.xlsx",
                 "crawl_report.csv", "crawl_report.xlsx", "sitemap.xml",
                 "sitemap.json", "crawl_state.json"):
        assert (root / name).is_file(), f"missing {name}"
    for name in ("broken_links.csv", "duplicates.csv", "external_links.csv", "inventory.json"):
        assert (root / "metadata" / name).is_file(), f"missing metadata/{name}"


async def test_broken_link_recorded(processed):
    _, root = processed
    text = (root / "metadata" / "broken_links.csv").read_text()
    assert "/missing" in text


async def test_external_links_inventory(processed):
    _, root = processed
    text = (root / "metadata" / "external_links.csv").read_text()
    assert "partner.com" in text


async def test_links_rewritten_for_offline(processed):
    _, root = processed
    html_files = list((root / "html").glob("*.html"))
    combined = "\n".join(f.read_text() for f in html_files)
    # No absolute internal links should remain after rewrite.
    assert "https://demo.local/about" not in combined
    assert ".html" in combined  # relative page links present


async def test_screenshots_captured(processed):
    summary, root = processed
    assert summary.screenshots == 5
    assert any((root / "screenshots").glob("*.png"))


async def test_state_marks_completed(processed):
    _, root = processed
    state = json.loads((root / "crawl_state.json").read_text())
    assert state["status"] == "completed"


async def test_resume_skips_completed(app_config):
    site = app_config.websites[0]
    await WebsiteProcessor(app_config, site, engine_factory=stub_factory).run()
    # Second run with resume should detect completion and not fail.
    summary = await WebsiteProcessor(
        app_config, site, resume=True, engine_factory=stub_factory
    ).run()
    assert summary.pages_downloaded == 5
