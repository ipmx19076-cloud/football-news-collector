"""Deduplication across sources and days.

Rule, applied in order to each incoming item:

1. URL: if the item's normalized `url_key` (tracking params, fragment, scheme, `www.`/`amp.`
   and trailing slash removed) is already stored, either as an item or as a recorded
   duplicate, it is skipped. This makes re-runs idempotent.
2. Headline: otherwise the headline is normalized (lowercase, accents and punctuation
   removed, a trailing " - <own publisher>" suffix dropped, stopwords removed, light
   stemming of -ing/-ed/-s). It is compared with
   stored items from a *different* source published within ±window_hours. It is the same
   story if token Jaccard >= jaccard_threshold or difflib ratio >= ratio_threshold.
   Headlines with fewer than `min_tokens` meaningful words must match exactly, so short
   generic titles ("Transfer news live") are not merged.
   A match is stored only in the `duplicates` table, linked to the first-seen item, so the
   digest can show "also reported by". Sources earlier in config.yaml win ties within a run.
3. Otherwise the item is new.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import timedelta
from difflib import SequenceMatcher
from enum import Enum

from .models import NewsItem
from .normalize import parse_datetime, to_iso
from .storage import Store

STOPWORDS = frozenset(
    """a an the and or but of to in on at for from by with as is are was were be been it its
    this that these those after before over into out up down v vs how why what who
    his her their our your he she they we you""".split()
)

_SEPARATORS = "-|–—:"
_POSSESSIVE = re.compile(r"(?<=\w)['’]s\b", re.IGNORECASE)
_NON_WORD = re.compile(r"[^a-z0-9 ]+")


@dataclass(frozen=True)
class DedupSettings:
    window_hours: float = 36
    jaccard_threshold: float = 0.8
    ratio_threshold: float = 0.9
    min_tokens: int = 4


class Outcome(str, Enum):
    NEW = "new"
    DUPLICATE_URL = "duplicate_url"
    DUPLICATE_STORY = "duplicate_story"


def _stem(token: str) -> str:
    """Very light suffix stripping so 'confirm'/'confirmed'/'confirms' compare equal."""
    for suffix in ("ing", "ed", "s"):
        if token.endswith(suffix) and len(token) - len(suffix) >= 4:
            return token[: -len(suffix)]
    return token


def publisher_of(source: str) -> str:
    """'BBC Sport (via NewsAPI)' -> 'BBC Sport'; plain feed names are returned unchanged."""
    return source.split(" (via ", 1)[0]


def normalize_headline(text: str, publisher: str | None = None) -> str:
    """Canonical form of a headline used only for similarity comparison.

    A trailing " - <publisher>" (as APIs append) is removed only when it names the item's own
    publisher, so real subtitles like "Arsenal news - Saka injury" are kept.
    """
    if publisher and text.lower().endswith(publisher.lower()):
        head = text[: -len(publisher)].rstrip()
        if head and head[-1] in _SEPARATORS:  # require a real separator, not just a space
            text = head.rstrip(_SEPARATORS)
    text = _POSSESSIVE.sub("", text)
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii").lower()
    text = text.replace("&", " and ").replace("'", "")
    tokens = [_stem(t) for t in _NON_WORD.sub(" ", text).split() if t not in STOPWORDS]
    return " ".join(tokens)


def similarity(a: str, b: str) -> tuple[float, float]:
    """(token Jaccard, character ratio) between two normalized headlines."""
    ta, tb = set(a.split()), set(b.split())
    jaccard = len(ta & tb) / len(ta | tb) if ta and tb else 0.0
    return jaccard, SequenceMatcher(None, a, b).ratio()


class Deduplicator:
    def __init__(self, store: Store, settings: DedupSettings | None = None) -> None:
        self.store = store
        self.settings = settings or DedupSettings()

    def find_story_match(self, item: NewsItem, norm: str) -> tuple[int, float] | None:
        """Return (item_id, score) of a stored item from another source with a matching headline."""
        published = parse_datetime(item.published_at)
        assert published is not None  # NewsItem timestamps are always valid ISO
        window = timedelta(hours=self.settings.window_hours)
        short = len(norm.split()) < self.settings.min_tokens
        best: tuple[int, float] | None = None
        for cand in self.store.candidates(to_iso(published - window), to_iso(published + window)):
            if cand.source == item.source:
                continue
            if short or len(cand.headline_norm.split()) < self.settings.min_tokens:
                if norm and norm == cand.headline_norm:
                    return cand.id, 1.0
                continue
            jaccard, ratio = similarity(norm, cand.headline_norm)
            if jaccard >= self.settings.jaccard_threshold or ratio >= self.settings.ratio_threshold:
                score = max(jaccard, ratio)
                if best is None or score > best[1]:
                    best = (cand.id, score)
        return best

    def add(self, item: NewsItem) -> Outcome:
        """Store item if new, record it as a duplicate sighting otherwise."""
        if self.store.url_key_exists(item.url_key):
            return Outcome.DUPLICATE_URL
        norm = normalize_headline(item.headline, publisher_of(item.source))
        match = self.find_story_match(item, norm)
        if match is not None:
            item_id, score = match
            self.store.add_duplicate(item_id, item, "headline", round(score, 3))
            return Outcome.DUPLICATE_STORY
        self.store.insert_item(item, norm)
        return Outcome.NEW
