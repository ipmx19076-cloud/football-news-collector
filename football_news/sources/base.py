"""Common interface for source adapters."""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from datetime import datetime

from ..config import SourceConfig
from ..models import FetchResult
from ..net import PoliteClient


class SourceSkipped(Exception):
    """The source cannot run this time for an expected reason (e.g. no API key)."""


class BaseSource(ABC):
    """An adapter turns one configured source into normalized NewsItems."""

    #: Adapter type name used in config.yaml (`type: rss`).
    type_name: str = ""

    def __init__(self, config: SourceConfig, *, summary_max_chars: int = 280) -> None:
        self.config = config
        self.summary_max_chars = summary_max_chars

    @property
    def name(self) -> str:
        return self.config.name

    def option(self, key: str, default: object = None) -> object:
        return self.config.options.get(key, default)

    def require_env(self, option_key: str) -> str:
        """Read the secret named by config option `option_key`; skip the source if unset."""
        env_name = self.option(option_key)
        if not env_name:
            raise SourceSkipped(f"'{option_key}' not configured")
        value = os.environ.get(str(env_name), "").strip()
        if not value:
            raise SourceSkipped(f"${env_name} is not set")
        return value

    @abstractmethod
    def fetch(self, client: PoliteClient, fetched_at: datetime) -> FetchResult:
        """Fetch and normalize items. Raise FetchError/SourceSkipped to skip this source."""
