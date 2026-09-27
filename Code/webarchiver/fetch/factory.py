"""Factory that builds the configured render engine with a shared ProxyManager."""
from __future__ import annotations

from webarchiver.config.models import GlobalConfig, RenderEngineType
from webarchiver.fetch.drission_engine import DrissionRenderEngine
from webarchiver.fetch.engine import RenderEngine
from webarchiver.fetch.http_fetcher import HttpRenderEngine
from webarchiver.proxy.manager import ProxyManager


def build_engine(
    engine_type: RenderEngineType,
    config: GlobalConfig,
    proxy_manager: ProxyManager | None = None,
) -> RenderEngine:
    """Return a concrete :class:`RenderEngine` for ``engine_type``.

    A :class:`ProxyManager` is created from ``config`` when not supplied, so the
    engine routes requests through managed residential-proxy sessions.
    """
    if proxy_manager is None:
        proxy_manager = ProxyManager(config)
    if engine_type is RenderEngineType.HTTP:
        return HttpRenderEngine(config, proxy_manager)
    if engine_type is RenderEngineType.DRISSION:
        primary = DrissionRenderEngine(config, headless=config.headless,
                                       proxy_manager=proxy_manager)
        if config.chrome_fallback_cloak and config.chrome_cloak_path:
            from webarchiver.fetch.fallback_engine import FallbackRenderEngine
            backup = DrissionRenderEngine(config, headless=config.headless,
                                          proxy_manager=proxy_manager,
                                          browser_path=config.chrome_cloak_path)
            return FallbackRenderEngine(primary, backup)
        return primary
    raise ValueError(f"Unsupported render engine: {engine_type}")
