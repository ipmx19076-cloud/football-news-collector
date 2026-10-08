"""Core data types shared by sources, storage and digest."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class NewsItem:
    """A normalized news record. Timestamps are ISO 8601 UTC strings ('...Z')."""

    headline: str
    source: str
    url: str                 # cleaned link (tracking params removed), still a working URL
    url_key: str             # stricter normalized form used only for deduplication
    published_at: str
    date_inferred: bool      # True when the feed gave no usable date and fetched_at was used
    summary: str
    fetched_at: str
    leagues: tuple[str, ...] = ()
    clubs: tuple[str, ...] = ()


@dataclass(slots=True)
class FetchResult:
    """What a source adapter returns for one fetch."""

    items: list[NewsItem] = field(default_factory=list)
    invalid: int = 0          # entries dropped for missing headline or link
    notes: list[str] = field(default_factory=list)
