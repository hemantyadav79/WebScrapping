"""Publish the self-contained property website into a site's output folder.

The template (index.html / style.css / app.js / serve.bat) ships inside the
package. After a scrape, these files are copied next to the freshly written
``listings.json`` so the folder is an immediately runnable website that reads
its own data. The HTML/CSS/JS never change with the data — they fetch
``listings.json`` at runtime — so a re-scrape only needs a page refresh.
"""
from __future__ import annotations

import shutil
from pathlib import Path

_TEMPLATE_DIR = Path(__file__).parent / "template"
_FILES = ("index.html", "style.css", "app.js", "serve.bat")


def publish_website(dest_dir: str | Path) -> int:
    """Copy the website template into ``dest_dir``. Returns files written."""
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    written = 0
    for name in _FILES:
        src = _TEMPLATE_DIR / name
        if src.is_file():
            shutil.copyfile(src, dest / name)
            written += 1
    return written
