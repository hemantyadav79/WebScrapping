"""Crawl frontier: the queue, the visited set and dedup bookkeeping.

Kept deliberately simple and serialisable so the entire crawl position can be
snapshotted to ``crawl_state.json`` and restored for ``--resume``.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass


@dataclass(slots=True, frozen=True)
class CrawlTask:
    """A single unit of crawl work."""

    url: str
    depth: int


class Frontier:
    """FIFO frontier with visited/enqueued tracking for BFS crawling."""

    def __init__(self) -> None:
        self._queue: deque[CrawlTask] = deque()
        self._enqueued: set[str] = set()
        self._visited: set[str] = set()

    def add(self, url: str, depth: int) -> bool:
        """Enqueue ``url`` if not already seen. Returns True if newly added."""
        if url in self._enqueued or url in self._visited:
            return False
        self._enqueued.add(url)
        self._queue.append(CrawlTask(url=url, depth=depth))
        return True

    def pop(self) -> CrawlTask | None:
        """Return the next task, or None when the queue is empty."""
        if not self._queue:
            return None
        task = self._queue.popleft()
        self._enqueued.discard(task.url)
        self._visited.add(task.url)
        return task

    def mark_visited(self, url: str) -> None:
        self._visited.add(url)

    def has_visited(self, url: str) -> bool:
        return url in self._visited

    def __len__(self) -> int:
        return len(self._queue)

    @property
    def visited_count(self) -> int:
        return len(self._visited)

    # ---- serialisation for resume -------------------------------------
    def to_dict(self) -> dict[str, object]:
        return {
            "queue": [[t.url, t.depth] for t in self._queue],
            "enqueued": sorted(self._enqueued),
            "visited": sorted(self._visited),
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "Frontier":
        frontier = cls()
        for url, depth in data.get("queue", []):  # type: ignore[union-attr]
            frontier._queue.append(CrawlTask(url=url, depth=int(depth)))
        frontier._enqueued = set(data.get("enqueued", []))  # type: ignore[arg-type]
        frontier._visited = set(data.get("visited", []))  # type: ignore[arg-type]
        return frontier
