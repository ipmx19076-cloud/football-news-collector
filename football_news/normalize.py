"""Turn raw feed/API fields into clean NewsItem records."""

from __future__ import annotations

import html
import logging
import re
import time
from datetime import datetime, timedelta, timezone, tzinfo
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from dateutil import parser as date_parser

from .models import NewsItem

log = logging.getLogger(__name__)

UTC = timezone.utc

# Common abbreviations dateutil cannot resolve on its own (offsets in seconds).
TZINFOS = {
    "UTC": 0, "GMT": 0, "Z": 0,
    "BST": 3600, "IST": 3600, "WET": 0, "WEST": 3600,
    "CET": 3600, "CEST": 7200, "EET": 7200, "EEST": 10800,
    "EST": -18000, "EDT": -14400, "CST": -21600, "CDT": -18000,
    "MST": -25200, "MDT": -21600, "PST": -28800, "PDT": -25200,
}

# Query parameters that only track the click; removing them never changes the article.
TRACKING_PARAMS = {
    "fbclid", "gclid", "dclid", "msclkid", "mc_cid", "mc_eid", "igshid",
    "cmpid", "cmp", "ito", "icid", "ocid", "ref", "ref_src", "rss", "src_rss",
    "s_cid", "intcmp", "xtor", "partner", "spref",
}
TRACKING_PREFIXES = ("utm_", "at_", "ns_", "pk_", "mtm_")

# Dates outside this range relative to fetch time are treated as unusable.
_MAX_FUTURE = timedelta(days=1)
_MIN_YEAR = 2000


class _TextExtractor(HTMLParser):
    """Collect text content, dropping tags and the bodies of script/style."""

    _SKIP = {"script", "style", "noscript", "iframe"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in self._SKIP:
            self._skip_depth += 1
        elif tag in {"br", "p", "div", "li"}:
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self.parts.append(data)


def strip_html(value: str | None) -> str:
    """Return plain text from an HTML fragment, with entities decoded and whitespace collapsed."""
    if not value:
        return ""
    extractor = _TextExtractor()
    try:
        extractor.feed(value)
        extractor.close()
        text = "".join(extractor.parts)
    except Exception:  # noqa: BLE001 - malformed markup: fall back to a crude tag strip
        text = re.sub(r"<[^>]*>", " ", value)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def truncate(text: str, max_chars: int) -> str:
    """Cut text to at most max_chars, on a word boundary, adding an ellipsis."""
    if len(text) <= max_chars:
        return text
    cut = text[: max_chars - 1]
    space = cut.rfind(" ")
    if space > max_chars * 0.6:
        cut = cut[:space]
    return cut.rstrip(" ,;:.-") + "…"


def clean_url(url: str) -> str:
    """Remove tracking parameters and the fragment; the result is still a valid link."""
    parts = urlsplit(url.strip())
    query = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if k.lower() not in TRACKING_PARAMS and not k.lower().startswith(TRACKING_PREFIXES)
    ]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))


def url_key(url: str) -> str:
    """Strict normalized form for deduplication (not for display)."""
    parts = urlsplit(clean_url(url))
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if host.startswith("amp."):
        host = host[4:]
    path = re.sub(r"/+", "/", parts.path).rstrip("/")
    if path.endswith("/amp"):
        path = path[: -len("/amp")]
    query = urlencode(sorted(parse_qsl(parts.query, keep_blank_values=True)))
    return urlunsplit(("https", host, path or "/", query, ""))


def parse_datetime(value: object, assume_tz: tzinfo | None = None) -> datetime | None:
    """Parse strings, epoch numbers, struct_time or datetime into an aware UTC datetime.

    `assume_tz` is for feeds that label local time with the wrong zone (e.g. ESPN writes
    "EST" all year): any zone in a string value is ignored and the time is read in assume_tz.
    """
    if value is None or value == "":
        return None
    try:
        if isinstance(value, datetime):
            dt = value
        elif isinstance(value, (int, float)):
            dt = datetime.fromtimestamp(float(value), tz=UTC)
        elif isinstance(value, time.struct_time):
            dt = datetime(*value[:6], tzinfo=UTC)  # feedparser's *_parsed values are UTC
        elif assume_tz is not None:
            dt = date_parser.parse(str(value), ignoretz=True).replace(tzinfo=assume_tz)
        else:
            dt = date_parser.parse(str(value), tzinfos=TZINFOS)
    except (ValueError, OverflowError, TypeError, date_parser.ParserError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)  # no zone given: assume UTC rather than local time
    return dt.astimezone(UTC)


def to_iso(dt: datetime) -> str:
    """Format as ISO 8601 UTC with a 'Z' suffix, second precision."""
    return dt.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def utc_now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def build_item(
    *,
    source: str,
    title: str | None,
    link: str | None,
    summary: str | None,
    published: object,
    fetched_at: datetime,
    max_summary_chars: int = 280,
    fallback_published: object = None,
    assume_tz: tzinfo | None = None,
) -> NewsItem | None:
    """Normalize one entry. Returns None when the headline or link is unusable."""
    headline = strip_html(title)
    link = (link or "").strip()
    if not headline or not link.lower().startswith(("http://", "https://")):
        log.debug("%s: dropping entry without headline/link (title=%r)", source, title)
        return None

    published_dt = parse_datetime(published, assume_tz)
    if published_dt is None and assume_tz is None:
        published_dt = parse_datetime(fallback_published)
    if published_dt and (
        published_dt > fetched_at + _MAX_FUTURE or published_dt.year < _MIN_YEAR
    ):
        log.debug("%s: implausible date %s for %r", source, published_dt, headline)
        published_dt = None
    date_inferred = published_dt is None

    return NewsItem(
        headline=headline,
        source=source,
        url=clean_url(link),
        url_key=url_key(link),
        published_at=to_iso(published_dt or fetched_at),
        date_inferred=date_inferred,
        summary=truncate(strip_html(summary), max_summary_chars),
        fetched_at=to_iso(fetched_at),
    )
