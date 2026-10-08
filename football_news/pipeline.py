"""Run every enabled source in isolation and report per-source outcomes."""

from __future__ import annotations

import logging
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime

from .config import Config
from .dedup import Deduplicator, Outcome
from .models import NewsItem
from .net import FetchError, PoliteClient, RateLimitedError, RobotsDisallowedError
from .normalize import to_iso, utc_now
from .sources import SourceSkipped, build_source
from .storage import Store
from .tagging import Tagger

log = logging.getLogger(__name__)


@dataclass
class SourceReport:
    name: str
    status: str = "ok"          # ok | skipped | failed | rate_limited | disallowed
    fetched: int = 0
    invalid: int = 0
    new: int = 0
    duplicates: int = 0
    requests: int = 0
    seconds: float = 0.0
    message: str = ""
    items: list[NewsItem] = field(default_factory=list, repr=False)


def collect(config: Config, client: PoliteClient, fetched_at: datetime | None = None) -> list[SourceReport]:
    """Fetch all enabled sources. A failure in one source never stops the others."""
    fetched_at = fetched_at or utc_now()
    reports: list[SourceReport] = []
    for source_cfg in config.enabled_sources:
        report = SourceReport(name=source_cfg.name)
        start, requests_before = time.monotonic(), client.request_count
        try:
            source = build_source(source_cfg, summary_max_chars=config.summary_max_chars)
            result = source.fetch(client, fetched_at)
        except SourceSkipped as exc:
            report.status, report.message = "skipped", str(exc)
            log.info("%s: skipped (%s)", source_cfg.name, exc)
        except RobotsDisallowedError as exc:
            report.status, report.message = "disallowed", str(exc)
            log.error("%s: NOT fetched - %s. Review this source's terms and disable it.", source_cfg.name, exc)
        except RateLimitedError as exc:
            report.status, report.message = "rate_limited", str(exc)
            log.warning("%s: %s", source_cfg.name, exc)
        except FetchError as exc:
            report.status, report.message = "failed", str(exc)
            log.warning("%s: failed - %s", source_cfg.name, exc)
        except Exception as exc:  # noqa: BLE001 - adapter bug must not stop other sources
            report.status, report.message = "failed", f"unexpected {type(exc).__name__}: {exc}"
            log.exception("%s: unexpected error", source_cfg.name)
        else:
            report.items = result.items
            report.fetched = len(result.items)
            report.invalid = result.invalid
            for note in result.notes:
                log.warning("%s: %s", source_cfg.name, note)
            inferred = sum(1 for i in result.items if i.date_inferred)
            log.info(
                "%s: fetched %d items (%d dropped as invalid, %d with inferred dates)",
                source_cfg.name, report.fetched, report.invalid, inferred,
            )
        report.requests = client.request_count - requests_before
        report.seconds = round(time.monotonic() - start, 2)
        reports.append(report)
    log.info("HTTP requests this run: %d", client.request_count)
    return reports


def store_items(reports: list[SourceReport], store: Store, dedup: Deduplicator,
                tagger: Tagger | None = None) -> None:
    """Tag, deduplicate and store each source's items; one transaction per source."""
    for report in reports:
        if not report.items:
            continue
        url_dupes = story_dupes = 0
        with store.transaction():
            for item in report.items:
                outcome = dedup.add(tagger.apply(item) if tagger else item)
                if outcome is Outcome.NEW:
                    report.new += 1
                elif outcome is Outcome.DUPLICATE_URL:
                    url_dupes += 1
                else:
                    story_dupes += 1
        report.duplicates = url_dupes + story_dupes
        log.info(
            "%s: %d new, %d duplicates (%d already stored by URL, %d same story elsewhere)",
            report.name, report.new, report.duplicates, url_dupes, story_dupes,
        )


def run(config: Config, client: PoliteClient, store: Store, tagger: Tagger | None = None) -> list[SourceReport]:
    """Full daily run: collect, tag, deduplicate, store, and record the run."""
    tagger = tagger or Tagger.from_config(config.raw.get("tagging"))  # fail fast on bad map
    started = utc_now()
    reports = collect(config, client, started)
    store_items(reports, store, Deduplicator(store, config.dedup), tagger)
    total_new = sum(r.new for r in reports)
    total_dupes = sum(r.duplicates for r in reports)
    store.record_run(
        started_at=to_iso(started),
        finished_at=to_iso(utc_now()),
        new_items=total_new,
        duplicates=total_dupes,
        requests=client.request_count,
        report=[{k: v for k, v in asdict(r).items() if k != "items"} for r in reports],
    )
    if total_new == 0:
        log.info("No new items this run (database has %d items)", store.count("items"))
    else:
        log.info("Stored %d new items (database now has %d)", total_new, store.count("items"))
    return reports
