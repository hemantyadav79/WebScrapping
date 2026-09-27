"""Application orchestrator: processes every configured website, then the master report."""
from __future__ import annotations

import asyncio
import logging

from webarchiver.config.models import AppConfig
from webarchiver.core.logging_setup import configure_root
from webarchiver.pipeline import WebsiteProcessor
from webarchiver.report.master import write_master_report
from webarchiver.report.models import SiteSummary

_log = logging.getLogger("webarchiver.app")


class Application:
    """Top-level runner. Each website is processed independently and in sequence."""

    def __init__(self, config: AppConfig, *, resume: bool = False) -> None:
        self._config = config
        self._resume = resume

    async def run_async(self) -> list[SiteSummary]:
        configure_root(self._config.global_config.log_level)
        summaries: list[SiteSummary] = []
        for site in self._config.websites:
            _log.info("=== Processing site: %s (%s) ===", site.name, site.url)
            try:
                processor = WebsiteProcessor(self._config, site, resume=self._resume)
                summaries.append(await processor.run())
            except Exception as exc:  # noqa: BLE001 - never let one site kill the run
                _log.exception("Unrecoverable error for site '%s': %s", site.name, exc)
                summary = SiteSummary(name=site.name, url=site.url)
                summary.errors.append(str(exc))
                summaries.append(summary)

        # Cross-site combined dataset (dedup) + market analytics.
        try:
            from webarchiver.report.combined import build_combined_outputs
            result = build_combined_outputs(self._config, _log)
            if result.get("combined"):
                _log.info(
                    "Combined dataset: %d unique, %d duplicates -> %s",
                    result["combined"], result["duplicates"], result.get("out_dir"),
                )
        except Exception as exc:  # noqa: BLE001 - combined output is best-effort
            _log.exception("Combined output step failed: %s", exc)

        # Optional: push data to Supabase for the dashboard (skips if no env).
        try:
            from webarchiver.report.supabase_upload import upload_to_supabase
            res = upload_to_supabase(self._config, _log)
            if res.get("uploaded"):
                _log.info("Supabase: uploaded %d rows to %s",
                          res["uploaded"], res.get("tables"))
        except Exception as exc:  # noqa: BLE001 - never let upload break the run
            _log.exception("Supabase upload step failed: %s", exc)

        master = write_master_report(summaries, self._config.global_config.output_root)
        _log.info("Master report written: %s", master)
        return summaries

    def run(self) -> list[SiteSummary]:
        return asyncio.run(self.run_async())
