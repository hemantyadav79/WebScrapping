"""SHA-256 hashing helpers for duplicate detection and integrity checks."""
from __future__ import annotations

import hashlib
from pathlib import Path

_CHUNK = 1024 * 1024


def sha256_bytes(data: bytes) -> str:
    """Return the SHA-256 hex digest of ``data``."""
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str, *, encoding: str = "utf-8") -> str:
    """Return the SHA-256 hex digest of ``text``."""
    return hashlib.sha256(text.encode(encoding, errors="replace")).hexdigest()


def sha256_file(path: str | Path) -> str:
    """Stream a file from disk and return its SHA-256 hex digest."""
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()
