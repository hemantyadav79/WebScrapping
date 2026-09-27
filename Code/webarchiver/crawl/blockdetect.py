"""Detect bot-protection challenge / block pages.

IMPORTANT: protected sites (e.g. realestate.com.au) embed their bot-protection
script (Kasada KPSDK) on *every* page, including legitimate ones. So the mere
presence of a vendor script name must NOT be treated as a block. Instead a page
is only considered blocked when it lacks real content AND shows a human-readable
challenge signal (or is an error status / implausibly tiny body).
"""
from __future__ import annotations

# Statuses that signal a block regardless of body.
_BLOCK_STATUS = {401, 403, 429, 503}

# Human-readable text that appears on actual challenge / interstitial pages.
_CHALLENGE_TEXT = (
    "just a moment",
    "checking your browser",
    "verify you are human",
    "please enable javascript and cookies",
    "attention required",
    "access denied",
    "unusual traffic",
    "px-captcha",
    "captcha-delivery",
    "/cdn-cgi/challenge-platform",
)

# Strong signals that the real content rendered (so it is NOT a challenge page).
_CONTENT_SIGNALS = (
    "__next_data__",
    "residential-card",
    "<article",
    'application/ld+json',
    "data-testid=\"results",
)

# A real results page is large; challenge interstitials are small.
_REAL_PAGE_MIN = 50_000


def is_blocked(html: str, status_code: int, *, min_size_bytes: int = 2048) -> tuple[bool, str]:
    """Return ``(blocked, reason)`` for a fetched page.

    A page is blocked when: the HTTP status is a known block code; the body is
    empty; or the body lacks real content AND looks like a challenge (challenge
    text present, or implausibly small).
    """
    if status_code in _BLOCK_STATUS:
        return True, f"HTTP {status_code}"
    if not html or not html.strip():
        return True, "empty body"

    low = html.lower()

    # Real content present (or a large document) => not a challenge, even if the
    # page also embeds the bot-protection script.
    if len(html) >= _REAL_PAGE_MIN or any(sig in low for sig in _CONTENT_SIGNALS):
        return False, ""

    for text in _CHALLENGE_TEXT:
        if text in low:
            return True, f"challenge text: {text!r}"

    if len(html) < min_size_bytes:
        return True, f"suspiciously small body ({len(html)} bytes)"

    return False, ""
