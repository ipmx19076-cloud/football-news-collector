"""League/club tagging from the editable keyword map in config.yaml.

Matching is case- and accent-insensitive on whole words ("Barça" matches "barca", "Inter"
does not match "internal"). Each entry may list `exclude` phrases that are blanked out before
its keywords are checked (e.g. "inter miami" for Inter). A club adds its configured league.
Leagues named explicitly in the text come first (they decide where the digest lists the item),
followed by leagues implied by clubs. Items with no match get `default_tag`. Nothing is
inferred beyond the map.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, replace
from typing import Any

from .config import ConfigError
from .models import NewsItem

DEFAULT_TAG = "Other/General"
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def fold(text: str) -> str:
    """Lowercase, strip accents, turn punctuation into spaces, pad with spaces for word matching."""
    text = text.replace("’", " ").replace("‘", " ")  # curly quotes would vanish in ASCII folding
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii").lower()
    return f" {_NON_ALNUM.sub(' ', text).strip()} "


@dataclass(frozen=True)
class Entity:
    name: str
    keywords: tuple[str, ...]          # folded, space-padded
    excludes: tuple[str, ...] = ()     # folded, space-padded
    league: str | None = None          # clubs only

    def matches(self, folded_text: str) -> bool:
        for phrase in self.excludes:
            folded_text = folded_text.replace(phrase, " | ")
        return any(k in folded_text for k in self.keywords)


def _entity(name: str, spec: Any, *, kind: str) -> Entity:
    if isinstance(spec, list):  # shorthand: Name: [kw1, kw2]
        spec = {"keywords": spec}
    if not isinstance(spec, dict) or not spec.get("keywords"):
        raise ConfigError(f"tagging.{kind}.{name} needs a non-empty 'keywords' list")
    keywords = tuple(fold(str(k)) for k in spec["keywords"])
    if any(k.strip() == "" for k in keywords):
        raise ConfigError(f"tagging.{kind}.{name} has an empty keyword")
    return Entity(
        name=name,
        keywords=keywords,
        excludes=tuple(fold(str(e)) for e in spec.get("exclude", []) or []),
        league=spec.get("league"),
    )


class Tagger:
    def __init__(self, leagues: list[Entity], clubs: list[Entity],
                 default_tag: str = DEFAULT_TAG, match_fields: tuple[str, ...] = ("headline", "summary")) -> None:
        names = {league.name for league in leagues}
        for club in clubs:
            if club.league and club.league not in names:
                raise ConfigError(f"club '{club.name}' refers to unknown league '{club.league}'")
        unknown = set(match_fields) - {"headline", "summary"}
        if unknown:
            raise ConfigError(f"tagging.match_fields: unknown field(s) {sorted(unknown)}")
        self.leagues = leagues
        self.clubs = clubs
        self.default_tag = default_tag
        self.match_fields = match_fields

    @classmethod
    def from_config(cls, raw: dict[str, Any] | None) -> Tagger:
        raw = raw or {}
        return cls(
            leagues=[_entity(n, s, kind="leagues") for n, s in (raw.get("leagues") or {}).items()],
            clubs=[_entity(n, s, kind="clubs") for n, s in (raw.get("clubs") or {}).items()],
            default_tag=str(raw.get("default_tag", DEFAULT_TAG)),
            match_fields=tuple(raw.get("match_fields", ("headline", "summary"))),
        )

    @property
    def league_order(self) -> list[str]:
        """League names in config order, with the default tag last (used by the digest)."""
        return [league.name for league in self.leagues] + [self.default_tag]

    def tag_text(self, headline: str, summary: str = "") -> tuple[tuple[str, ...], tuple[str, ...]]:
        parts = {"headline": headline, "summary": summary}
        text = " ".join(fold(parts[f]) for f in self.match_fields)
        clubs = [c for c in self.clubs if c.matches(text)]
        leagues = [lg.name for lg in self.leagues if lg.matches(text)]
        for club in clubs:
            if club.league and club.league not in leagues:
                leagues.append(club.league)
        return tuple(leagues) or (self.default_tag,), tuple(sorted(c.name for c in clubs))

    def apply(self, item: NewsItem) -> NewsItem:
        leagues, clubs = self.tag_text(item.headline, item.summary)
        return replace(item, leagues=leagues, clubs=clubs)
