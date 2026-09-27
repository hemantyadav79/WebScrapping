"""Master report aggregating every website processed in a run."""
from __future__ import annotations

from pathlib import Path

from webarchiver.report.exporters import to_excel, to_json
from webarchiver.report.models import SiteSummary


def write_master_report(summaries: list[SiteSummary], output_root: str | Path) -> Path:
    """Write ``master_report.xlsx`` (and a JSON sibling) for all sites."""
    output_root = Path(output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    rows = [s.as_row() for s in summaries]

    totals = {
        "Website": "TOTAL", "URL": f"{len(summaries)} site(s)",
        "Pages Discovered": sum(s.pages_discovered for s in summaries),
        "Pages Downloaded": sum(s.pages_downloaded for s in summaries),
        "Pages Failed": sum(s.pages_failed for s in summaries),
        "Images Downloaded": sum(s.images_downloaded for s in summaries),
        "Videos Downloaded": sum(s.videos_downloaded for s in summaries),
        "Documents Downloaded": sum(s.documents_downloaded for s in summaries),
        "Broken Links": sum(s.broken_links for s in summaries),
        "Total Size (bytes)": sum(s.total_size_bytes for s in summaries),
    }
    rows_with_total = rows + [totals]

    path = output_root / "master_report.xlsx"
    to_excel({"sites": rows_with_total}, path)
    to_json(rows_with_total, output_root / "master_report.json")
    return path
