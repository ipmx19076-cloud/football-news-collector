"""GNews.io adapter (https://gnews.io/docs/v4#search-endpoint). One request per run."""

from __future__ import annotations

from datetime import datetime

from ..models import FetchResult
from ..net import FetchError, PoliteClient
from ..normalize import build_item
from .base import BaseSource

ENDPOINT = "https://gnews.io/api/v4/search"


class GNewsSource(BaseSource):
    type_name = "gnews"

    def fetch(self, client: PoliteClient, fetched_at: datetime) -> FetchResult:
        api_key = self.require_env("api_key_env")
        params = {
            "q": str(self.option("query", "football OR soccer")),
            "lang": str(self.option("language", "en")),
            "max": min(self.config.max_items, 100),
            "sortby": "publishedAt",
            "apikey": api_key,  # GNews takes the key as a query param; net.redact() masks it in logs
        }
        resp = client.get(
            ENDPOINT, params=params, timeout=self.config.timeout, check_robots=False
        )
        try:
            data = resp.json()
        except ValueError as exc:
            raise FetchError("GNews returned non-JSON response") from exc
        if data.get("errors"):
            raise FetchError(f"GNews error: {data['errors']}")
        return self.parse(data, fetched_at)

    def parse(self, data: dict, fetched_at: datetime) -> FetchResult:
        result = FetchResult()
        for art in data.get("articles", [])[: self.config.max_items]:
            publisher = (art.get("source") or {}).get("name") or "unknown"
            item = build_item(
                source=f"{publisher} (via {self.name})",
                title=art.get("title"),
                link=art.get("url"),
                summary=art.get("description"),
                published=art.get("publishedAt"),
                fetched_at=fetched_at,
                max_summary_chars=self.summary_max_chars,
            )
            if item is None:
                result.invalid += 1
            else:
                result.items.append(item)
        return result
