"""Residential-proxy orchestration: provider formatting, sessions, failover.

Mirrors the PropFlow proxy design: each commercial provider encodes the target
country and a session id into the username in its own format. Sticky sessions
reuse one residential IP for a TTL (mimicking a real user); rotating sessions
mint a fresh IP per request. Sessions that accumulate failures are quarantined
and replaced.
"""
from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:  # pragma: no cover
    from webarchiver.config.models import GlobalConfig

# provider -> function(base_username, country, session_id) -> formatted username
_PROVIDER_FORMATTERS: dict[str, Callable[[str, str, str], str]] = {
    "brightdata": lambda u, cc, sid: f"{u}-country-{cc}-session-{sid}",
    "oxylabs": lambda u, cc, sid: f"{u}-cc-{cc}-sessid-{sid}",
    "smartproxy": lambda u, cc, sid: f"{u}-country-{cc}-session-{sid}",
    "netnut": lambda u, cc, sid: f"{u}-cc-{cc}-sid-{sid}",
    "custom": lambda u, cc, sid: u,
    "generic": lambda u, cc, sid: u,
}


@dataclass(slots=True)
class _Session:
    id: str
    created_at: float
    failures: int = 0


@dataclass(slots=True)
class ProxyHealth:
    rotations: int = 0
    successes: int = 0
    blocks: int = 0
    tunnel_failures: int = 0
    current_session: str = ""

    def as_dict(self) -> dict:
        return {
            "rotations": self.rotations,
            "successful_requests": self.successes,
            "blocked_requests": self.blocks,
            "tunnel_failures": self.tunnel_failures,
            "current_session": self.current_session,
        }


class ProxyManager:
    """Builds proxy URLs and manages session lifecycle. Thread-safe."""

    def __init__(self, config: "GlobalConfig") -> None:
        self._direct = (config.proxy or "").strip()
        self._provider = (config.proxy_provider or "none").lower()
        self._host = config.proxy_host
        self._port = config.proxy_port
        self._user = config.proxy_username
        self._password = config.proxy_password
        self._country = (config.proxy_country or "au").lower()
        self._rotating = config.proxy_session_type.lower() == "rotating"
        self._ttl = max(1, config.proxy_session_ttl_mins) * 60
        self._rotate_on_fail = config.proxy_rotate_on_fail
        self._max_failures = max(1, config.proxy_max_failures)
        self._session: _Session | None = None
        self._health = ProxyHealth()
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        if self._direct:
            return True
        return self._provider not in ("none", "") and bool(self._host)

    @staticmethod
    def _new_id() -> str:
        return secrets.token_hex(6)

    def _build_url(self, session_id: str) -> str:
        formatter = _PROVIDER_FORMATTERS.get(self._provider, _PROVIDER_FORMATTERS["generic"])
        username = formatter(self._user, self._country, session_id)
        cred = ""
        if username:
            cred = f"{username}:{self._password}@" if self._password else f"{username}@"
        port = f":{self._port}" if self._port else ""
        return f"http://{cred}{self._host}{port}"

    def current(self) -> str | None:
        """Return the proxy URL to use now (or None if proxying is disabled)."""
        if not self.enabled:
            return None
        if self._direct:
            return self._direct
        with self._lock:
            if self._rotating:
                self._health.rotations += 1
                sid = self._new_id()
                self._health.current_session = sid
                return self._build_url(sid)
            now = time.time()
            expired = self._session is not None and (now - self._session.created_at) > self._ttl
            quarantined = self._session is not None and self._session.failures >= self._max_failures
            if self._session is None or expired or quarantined:
                self._session = _Session(id=self._new_id(), created_at=now)
                self._health.rotations += 1
            self._health.current_session = self._session.id
            return self._build_url(self._session.id)

    def report_success(self) -> None:
        with self._lock:
            self._health.successes += 1

    def report_failure(self, *, blocked: bool = False) -> None:
        """Record a failure; quarantines the sticky session if configured."""
        with self._lock:
            if blocked:
                self._health.blocks += 1
            else:
                self._health.tunnel_failures += 1
            if self._rotate_on_fail and self._session is not None:
                self._session.failures += 1

    def get_health_report(self) -> dict:
        with self._lock:
            return {"enabled": self.enabled, "provider": self._provider, **self._health.as_dict()}
