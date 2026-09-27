"""Generate sitemap.xml (search-engine format) and sitemap.json (hierarchy)."""
from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit
from xml.dom import minidom
from xml.etree import ElementTree as ET

from webarchiver.extract.models import PageRecord
from webarchiver.report.exporters import to_json

_NS = "http://www.sitemaps.org/schemas/sitemap/0.9"


def _healthy(pages: list[PageRecord]) -> list[PageRecord]:
    return [p for p in pages if not p.error]


def write_sitemap_xml(pages: list[PageRecord], path: str | Path) -> Path:
    """Write a standards-compliant XML sitemap of crawled pages."""
    urlset = ET.Element("urlset", xmlns=_NS)
    for page in _healthy(pages):
        url_el = ET.SubElement(urlset, "url")
        ET.SubElement(url_el, "loc").text = page.final_url or page.url
    rough = ET.tostring(urlset, encoding="utf-8")
    pretty = minidom.parseString(rough).toprettyxml(indent="  ", encoding="utf-8")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(pretty)
    return path


def _build_tree(pages: list[PageRecord]) -> dict:
    """Build a nested hierarchy keyed by URL path segments."""
    root: dict = {"name": "/", "url": None, "children": {}}
    for page in _healthy(pages):
        parts = urlsplit(page.final_url or page.url)
        node = root
        segments = [s for s in parts.path.split("/") if s] or ["index"]
        for seg in segments:
            children = node["children"]
            if seg not in children:
                children[seg] = {"name": seg, "url": None, "children": {}}
            node = children[seg]
        node["url"] = page.final_url or page.url
        node["title"] = page.title
        node["local_path"] = page.local_path
    return root


def _serialise(node: dict) -> dict:
    return {
        "name": node["name"],
        "url": node.get("url"),
        "title": node.get("title", ""),
        "local_path": node.get("local_path", ""),
        "children": [_serialise(child) for child in node["children"].values()],
    }


def write_sitemap_json(pages: list[PageRecord], path: str | Path) -> Path:
    """Write a hierarchical JSON sitemap of crawled pages."""
    tree = _serialise(_build_tree(pages))
    return to_json(tree, path)
