"""Ollama-powered link classification.

Sends batches of discovered URLs to a locally running Ollama model and asks it
to drop advertising / tracking / junk links while keeping real content pages
(including pagination). The filter is strictly *optional*:

- if the Ollama server is unreachable it disables itself after the first
  failure and the crawl continues with the heuristic filter alone;
- if the model returns malformed output, the batch is kept unchanged
  (fail-open: never lose real content links because of an LLM hiccup).

Uses only the standard library (urllib in a worker thread), so the crawler
gains no new hard dependency.
"""
from __future__ import annotations

import asyncio
import json
import logging
import urllib.error
import urllib.request

_BATCH_SIZE = 40

_PROMPT = """You are helping a website archiver decide which discovered URLs to crawl.
Given the JSON array of URLs below (all from the same website), identify the indexes of URLs
that should be SKIPPED because they are clearly one of:
- advertising, ad-server, sponsored or banner links
- tracking pixels / analytics endpoints
- login, logout, signup, cart or account/session actions
- endless calendar / faceted-filter traps (e.g. dozens of sort/filter permutations)

KEEP everything else, especially: articles, products, listings, category pages,
pagination pages (page=2, /page/3 ...), about/contact pages, documents.
If unsure, KEEP the URL.

URLs:
{urls}

Respond with ONLY a JSON object: {{"skip": [<indexes to skip>]}}"""


class OllamaLinkFilter:
    """Classifies crawl candidates with a local Ollama model. Fail-open."""

    def __init__(
        self,
        host: str = "http://localhost:11434",
        model: str = "llama3.2",
        timeout: int = 20,
        logger: logging.Logger | None = None,
    ) -> None:
        self._host = host.rstrip("/")
        self._model = model
        self._timeout = max(1, timeout)
        self._log = logger or logging.getLogger("webarchiver.llm")
        self._enabled = True
        self._cache: dict[str, bool] = {}  # url -> keep?

    # ---- availability --------------------------------------------------
    def _check_available_sync(self) -> bool:
        try:
            req = urllib.request.Request(f"{self._host}/api/tags", method="GET")
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="replace"))
            models = [m.get("name", "") for m in data.get("models", [])]
            if not any(m.split(":")[0] == self._model.split(":")[0] for m in models):
                self._log.warning(
                    "Ollama is running but model '%s' is not pulled (available: %s). "
                    "Run `ollama pull %s`. Falling back to heuristic filtering.",
                    self._model, ", ".join(models) or "none", self._model,
                )
                return False
            return True
        except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
            return False

    async def start(self) -> None:
        """Probe the Ollama server once; disable the filter if unreachable."""
        self._enabled = await asyncio.to_thread(self._check_available_sync)
        if self._enabled:
            self._log.info("Ollama link filter active (model=%s, host=%s)", self._model, self._host)
        else:
            self._log.info("Ollama not available — using heuristic link filtering only.")

    @property
    def enabled(self) -> bool:
        return self._enabled

    # ---- classification -------------------------------------------------
    def _generate_sync(self, prompt: str) -> str:
        payload = json.dumps({
            "model": self._model,
            "prompt": prompt,
            "stream": False,
            "format": "json",
            "options": {"temperature": 0},
        }).encode("utf-8")
        req = urllib.request.Request(
            f"{self._host}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self._timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
        return data.get("response", "")

    async def filter_links(self, links: list[str]) -> list[str]:
        """Return ``links`` minus those the model classifies as junk/ads."""
        if not self._enabled or not links:
            return links

        unknown = [u for u in links if u not in self._cache]
        for i in range(0, len(unknown), _BATCH_SIZE):
            batch = unknown[i : i + _BATCH_SIZE]
            await self._classify_batch(batch)
            if not self._enabled:  # server died mid-crawl -> keep the rest
                break

        return [u for u in links if self._cache.get(u, True)]

    async def _classify_batch(self, batch: list[str]) -> None:
        prompt = _PROMPT.format(urls=json.dumps(list(batch), indent=0))
        try:
            raw = await asyncio.to_thread(self._generate_sync, prompt)
            parsed = json.loads(raw)
            skip = parsed.get("skip", [])
            skip_idx = {int(i) for i in skip if str(i).lstrip("-").isdigit()}
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            self._log.warning("Ollama unreachable (%s); disabling LLM filter.", exc)
            self._enabled = False
            for url in batch:
                self._cache[url] = True
            return
        except (json.JSONDecodeError, ValueError, TypeError):
            self._log.debug("Ollama returned malformed output; keeping batch unchanged.")
            for url in batch:
                self._cache[url] = True
            return

        for idx, url in enumerate(batch):
            keep = idx not in skip_idx
            self._cache[url] = keep
            if not keep:
                self._log.info("LLM filtered out: %s", url)
