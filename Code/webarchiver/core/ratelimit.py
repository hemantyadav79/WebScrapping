"""Async token-bucket rate limiter and retry helper."""
from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Awaitable, Callable
from typing import TypeVar

T = TypeVar("T")


class AsyncRateLimiter:
    """Token-bucket limiter capping operations to ``rate`` per second."""

    def __init__(self, rate_per_sec: float, *, burst: float | None = None) -> None:
        if rate_per_sec <= 0:
            raise ValueError("rate_per_sec must be > 0")
        self._rate = rate_per_sec
        self._capacity = burst if burst is not None else max(1.0, rate_per_sec)
        self._tokens = self._capacity
        self._updated = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """Block until a token is available, then consume it."""
        async with self._lock:
            while True:
                now = time.monotonic()
                elapsed = now - self._updated
                self._updated = now
                self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                deficit = (1.0 - self._tokens) / self._rate
                await asyncio.sleep(deficit)


async def retry_async(
    func: Callable[[], Awaitable[T]],
    *,
    retries: int = 3,
    backoff: float = 0.5,
    exceptions: tuple[type[BaseException], ...] = (Exception,),
    on_retry: Callable[[int, BaseException], None] | None = None,
) -> T:
    """Call ``func`` with exponential backoff and jitter on failure.

    Raises the last exception if all attempts fail.
    """
    attempt = 0
    while True:
        try:
            return await func()
        except exceptions as exc:  # noqa: BLE001 - deliberate broad retry boundary
            attempt += 1
            if attempt > retries:
                raise
            if on_retry is not None:
                on_retry(attempt, exc)
            delay = backoff * (2 ** (attempt - 1)) + random.uniform(0, backoff)
            await asyncio.sleep(delay)
