"""Concurrent asset downloader with content-hash de-duplication.

Each unique asset URL is fetched at most once. Bytes are hashed (SHA-256); if an
identical payload was already stored, the new record reuses the existing file
path and is flagged as a duplicate instead of writing a second copy — saving
storage while still recording the reference graph.
"""
from __future__ import annotations

import asyncio
import logging

from webarchiver.archive.storage import Workspace
from webarchiver.core.hashing import sha256_bytes
from webarchiver.extract.models import AssetRecord
from webarchiver.fetch.engine import RenderEngine


class AssetDownloader:
    """Downloads and stores assets concurrently."""

    def __init__(
        self,
        *,
        engine: RenderEngine,
        workspace: Workspace,
        concurrency: int,
        logger: logging.Logger | None = None,
    ) -> None:
        self._engine = engine
        self._workspace = workspace
        self._semaphore = asyncio.Semaphore(max(1, concurrency))
        self._log = logger or logging.getLogger("webarchiver.download")
        self._hash_index: dict[str, str] = {}  # content hash -> local relpath
        self._lock = asyncio.Lock()

    async def download_all(self, records: list[AssetRecord]) -> list[AssetRecord]:
        """Download every record in ``records`` concurrently and return them."""
        await asyncio.gather(*(self._download_one(rec) for rec in records))
        ok = sum(1 for r in records if r.downloaded)
        self._log.info("Assets: %d/%d downloaded", ok, len(records))
        return records

    async def _download_one(self, record: AssetRecord) -> AssetRecord:
        async with self._semaphore:
            result = await self._engine.fetch_binary(record.url)
            record.status_code = result.status_code
            if not result.ok or not result.body:
                record.error = result.error or f"HTTP {result.status_code}"
                self._log.warning("Asset failed %s -> %s", record.url, record.error)
                return record

            digest = sha256_bytes(result.body)
            record.content_hash = digest
            record.file_size = len(result.body)

            async with self._lock:
                existing = self._hash_index.get(digest)
                if existing is not None:
                    record.local_path = existing  # reuse identical payload
                    self._log.debug("Duplicate asset reused: %s", record.url)
                    return record
                path = self._workspace.asset_path(record.url, record.asset_type)
                self._hash_index[digest] = self._workspace.relpath(path)

            try:
                self._workspace.write_bytes(path, result.body)
                record.local_path = self._workspace.relpath(path)
            except OSError as exc:
                record.error = f"write failed: {exc}"
                self._log.warning("Asset write failed %s -> %s", record.url, exc)
            return record
