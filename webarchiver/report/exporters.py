"""Tabular export helpers for CSV, Excel and JSON outputs."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pandas as pd


def to_csv(rows: list[dict[str, Any]], path: str | Path, *, columns: list[str] | None = None) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(rows, columns=columns) if rows else pd.DataFrame(columns=columns or [])
    df.to_csv(path, index=False, encoding="utf-8")
    return path


def to_excel(
    sheets: dict[str, list[dict[str, Any]]],
    path: str | Path,
    *,
    columns: dict[str, list[str]] | None = None,
) -> Path:
    """Write one or more named sheets to a single ``.xlsx`` workbook."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = columns or {}
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        for name, rows in sheets.items():
            cols = columns.get(name)
            df = pd.DataFrame(rows, columns=cols) if rows else pd.DataFrame(columns=cols or [])
            # Excel sheet names are capped at 31 characters.
            df.to_excel(writer, sheet_name=name[:31], index=False)
    return path


def to_json(data: Any, path: str | Path) -> Path:
    """Atomically write JSON: serialise first, write to a temp file, then replace.

    The destination file is NEVER truncated by this call. If serialisation or the
    temp write fails (e.g. disk full / process killed), the existing file is left
    intact and the partial temp file is removed. This prevents data loss on save.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, indent=2, ensure_ascii=False, default=str)  # build BEFORE touching the file
    tmp = path.with_name(path.name + ".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)  # atomic on Windows + POSIX
    except Exception:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        raise
    return path
