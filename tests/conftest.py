"""Shared pytest fixtures."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Ensure the project root is importable when running `pytest` from anywhere.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from webarchiver.config.models import AppConfig, GlobalConfig, RenderEngineType, WebsiteConfig


@pytest.fixture
def demo_site() -> WebsiteConfig:
    return WebsiteConfig(
        name="DemoSite",
        url="https://demo.local",
        max_depth=5,
        take_screenshots=True,
        render_engine=RenderEngineType.HTTP,
    )


@pytest.fixture
def app_config(tmp_path, demo_site) -> AppConfig:
    gc = GlobalConfig(output_root=str(tmp_path / "website_backup"), concurrency=4, log_level="WARNING")
    return AppConfig(global_config=gc, websites=[demo_site])
