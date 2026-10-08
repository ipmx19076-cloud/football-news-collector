"""Source adapter registry. Add a new adapter type by registering its class here."""

from __future__ import annotations

from ..config import ConfigError, SourceConfig
from .base import BaseSource, SourceSkipped
from .gnews import GNewsSource
from .newsapi import NewsApiSource
from .reddit import RedditSource
from .rss import RssSource

ADAPTERS: dict[str, type[BaseSource]] = {
    cls.type_name: cls for cls in (RssSource, NewsApiSource, GNewsSource, RedditSource)
}


def build_source(config: SourceConfig, *, summary_max_chars: int = 280) -> BaseSource:
    try:
        adapter = ADAPTERS[config.type]
    except KeyError:
        raise ConfigError(
            f"source '{config.name}' has unknown type '{config.type}' "
            f"(known: {', '.join(sorted(ADAPTERS))})"
        ) from None
    return adapter(config, summary_max_chars=summary_max_chars)


__all__ = ["ADAPTERS", "BaseSource", "SourceSkipped", "build_source"]
