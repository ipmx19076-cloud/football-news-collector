"""RSS/Atom feed adapter."""

from __future__ import annotations

import gzip
import logging
from datetime import datetime

import feedparser
from dateutil import tz

from ..config import ConfigError
from ..models import FetchResult
from ..net import FetchError, PoliteClient
from ..normalize import build_item
from .base import BaseSource

log = logging.getLogger(__name__)


def parse_feed_bytes(body: bytes, content_type: str = "") -> feedparser.FeedParserDict:
    """Parse feed bytes, transparently handling gzip bodies sent without Content-Encoding."""
    if body[:2] == b"\x1f\x8b":
        try:
            body = gzip.decompress(body)
        except OSError as exc:
            raise FetchError(f"corrupt gzip body: {exc}") from exc
    headers = {"content-type": content_type} if content_type else {}
    return feedparser.parse(body, response_headers=headers)


class RssSource(BaseSource):
    type_name = "rss"

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        tz_name = self.option("assume_timezone")
        self.assume_tz = tz.gettz(str(tz_name)) if tz_name else None
        if tz_name and self.assume_tz is None:
            raise ConfigError(f"source '{self.name}': unknown assume_timezone '{tz_name}'")

    def fetch(self, client: PoliteClient, fetched_at: datetime) -> FetchResult:
        assert self.config.url  # validated in config loading
        resp = client.get(self.config.url, timeout=self.config.timeout)
        return self.parse(resp.content, fetched_at, resp.headers.get("content-type", ""))

    def parse(self, body: bytes, fetched_at: datetime, content_type: str = "") -> FetchResult:
        feed = parse_feed_bytes(body, content_type)
        result = FetchResult()
        if feed.bozo and not feed.entries:
            raise FetchError(f"malformed or non-feed response ({feed.get('bozo_exception')})")
        if feed.bozo:
            result.notes.append(f"feed had parse warnings: {feed.get('bozo_exception')}")
        if not feed.entries:
            result.notes.append("feed contained no entries")
            return result

        for entry in feed.entries[: self.config.max_items]:
            item = build_item(
                source=self.name,
                title=entry.get("title"),
                link=entry.get("link"),
                summary=entry.get("summary") or entry.get("description"),
                published=entry.get("published") or entry.get("updated"),
                fallback_published=entry.get("published_parsed") or entry.get("updated_parsed"),
                fetched_at=fetched_at,
                max_summary_chars=self.summary_max_chars,
                assume_tz=self.assume_tz,
            )
            if item is None:
                result.invalid += 1
            else:
                result.items.append(item)
        return result
