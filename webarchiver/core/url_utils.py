"""URL validation, normalisation and same-domain logic.

Normalisation is critical to avoid crawling the same logical page twice
(``http://a.com/x``, ``http://a.com/x/``, ``http://a.com/x#frag`` etc.).
"""
from __future__ import annotations

from urllib.parse import (
    parse_qsl,
    quote,
    unquote,
    urldefrag,
    urljoin,
    urlsplit,
    urlunsplit,
)

_DEFAULT_PORTS = {"http": "80", "https": "443"}
# Query params commonly used for tracking that do not change page content.
_TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "gclid", "fbclid", "mc_cid", "mc_eid", "_ga",
}


def is_valid_http_url(url: str) -> bool:
    """Return True if ``url`` is a syntactically valid http(s) URL with a host."""
    if not url or not isinstance(url, str):
        return False
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return False
    return parts.scheme in ("http", "https") and bool(parts.netloc)


def normalize_url(url: str, *, drop_tracking: bool = True) -> str:
    """Return a canonical form of ``url``.

    - lowercases scheme and host
    - removes default ports and fragments
    - sorts query parameters and optionally strips tracking params
    - collapses duplicate slashes in the path
    """
    url, _frag = urldefrag(url.strip())
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    host = parts.hostname.lower() if parts.hostname else ""

    port = parts.port
    netloc = host
    if port is not None and str(port) != _DEFAULT_PORTS.get(scheme):
        netloc = f"{host}:{port}"
    if parts.username:
        cred = parts.username
        if parts.password:
            cred = f"{cred}:{parts.password}"
        netloc = f"{cred}@{netloc}"

    path = parts.path or "/"
    while "//" in path:
        path = path.replace("//", "/")
    path = quote(unquote(path), safe="/%:@!$&'()*+,;=~-._")

    query_pairs = parse_qsl(parts.query, keep_blank_values=True)
    if drop_tracking:
        query_pairs = [(k, v) for k, v in query_pairs if k.lower() not in _TRACKING_PARAMS]
    query = "&".join(f"{k}={v}" for k, v in sorted(query_pairs))

    return urlunsplit((scheme, netloc, path, query, ""))


def resolve_url(base: str, link: str) -> str | None:
    """Resolve a possibly relative ``link`` against ``base``; None if unusable."""
    if not link:
        return None
    link = link.strip()
    low = link.lower()
    if low.startswith(("mailto:", "tel:", "javascript:", "data:", "#")):
        return None
    try:
        resolved = urljoin(base, link)
    except ValueError:
        return None
    return resolved if is_valid_http_url(resolved) else None


def registrable_host(url: str) -> str:
    """Return the lowercased hostname of ``url`` (empty string if none)."""
    host = urlsplit(url).hostname
    return host.lower() if host else ""


def is_same_domain(url: str, root_host: str, *, include_subdomains: bool = False) -> bool:
    """Return True if ``url`` belongs to ``root_host`` (optionally its subdomains)."""
    host = registrable_host(url)
    if not host or not root_host:
        return False
    root_host = root_host.lower()
    if host == root_host:
        return True
    if include_subdomains:
        return host.endswith("." + root_host)
    # Treat www as equivalent to the bare domain.
    return host == f"www.{root_host}" or root_host == f"www.{host}"


def url_path_segments(url: str) -> list[str]:
    """Return non-empty path segments of ``url``."""
    return [seg for seg in urlsplit(url).path.split("/") if seg]
