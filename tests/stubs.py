"""Reusable in-memory fixture site and a stub RenderEngine for tests.

The stub serves a small deterministic website (pages + assets) entirely from
memory, so the full pipeline can be exercised offline without a browser or
network access.
"""
from __future__ import annotations

from pathlib import Path

from webarchiver.fetch.engine import FetchResult, RenderEngine

BASE = "https://demo.local"

# Identical bytes for logo + item image to exercise duplicate detection.
_PNG = b"\x89PNG\r\n\x1a\nDEMOIMAGE"

PAGES: dict[str, str] = {
    f"{BASE}/": """
        <html lang="en"><head><title>Home</title>
        <meta name="description" content="Demo home">
        <meta name="keywords" content="demo,home">
        <link rel="canonical" href="https://demo.local/">
        <meta property="og:title" content="Demo Home">
        <script type="application/ld+json">{"@type":"WebSite","name":"Demo"}</script>
        <link rel="stylesheet" href="/style.css">
        <script src="/app.js"></script></head>
        <body><h1>Welcome</h1><h2>Sub</h2>
        <img src="/img/logo.png">
        <a href="/about">About</a>
        <a href="/products">Products</a>
        <a href="/contact">Contact</a>
        <a href="https://partner.com/x">Partner</a>
        <a href="/files/guide.pdf">Guide</a></body></html>""",
    f"{BASE}/about": """
        <html lang="en"><head><title>About</title></head>
        <body><h1>About</h1><a href="/">Home</a><a href="/products">Products</a></body></html>""",
    f"{BASE}/products": """
        <html lang="en"><head><title>Products</title></head>
        <body><h1>Products</h1><img src="/img/item.png">
        <a href="/products/item1">Item 1</a><a href="/">Home</a></body></html>""",
    f"{BASE}/products/item1": """
        <html lang="en"><head><title>Item 1</title></head>
        <body><h1>Item 1</h1><a href="/products">Back</a></body></html>""",
    f"{BASE}/contact": """
        <html lang="en"><head><title>Contact</title></head>
        <body><h1>Contact</h1><a href="/missing">Dead link</a></body></html>""",
}

ASSETS: dict[str, bytes] = {
    f"{BASE}/style.css": b"body{font-family:sans-serif}",
    f"{BASE}/app.js": b"console.log('demo')",
    f"{BASE}/img/logo.png": _PNG,
    f"{BASE}/img/item.png": _PNG,  # duplicate of logo
    f"{BASE}/files/guide.pdf": b"%PDF-1.4 demo document",
}


class StubRenderEngine(RenderEngine):
    """Serves :data:`PAGES` / :data:`ASSETS` from memory; 404 for anything else."""

    def __init__(self, *args, **kwargs) -> None:
        self.started = False

    async def start(self) -> None:
        self.started = True

    async def fetch(self, url: str) -> FetchResult:
        if url in PAGES:
            return FetchResult(
                url=url, final_url=url, status_code=200,
                content_type="text/html", html=PAGES[url],
            )
        return FetchResult(url=url, final_url=url, status_code=404, error="HTTP 404")

    async def fetch_binary(self, url: str) -> FetchResult:
        if url in ASSETS:
            return FetchResult(
                url=url, final_url=url, status_code=200,
                content_type="application/octet-stream", body=ASSETS[url],
            )
        return FetchResult(url=url, final_url=url, status_code=404, error="HTTP 404")

    async def screenshot(self, url: str, dest_path: str) -> bool:
        Path(dest_path).write_bytes(b"\x89PNG\r\n\x1a\nSCREENSHOT")
        return True

    async def close(self) -> None:
        self.started = False


def stub_factory(*_args, **_kwargs) -> StubRenderEngine:
    """Engine factory compatible with ``WebsiteProcessor(engine_factory=...)``."""
    return StubRenderEngine()
