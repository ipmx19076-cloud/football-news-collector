"""NewsAPI.org adapter (https://newsapi.org/docs/endpoints/everything). One request per run."""

from __future__ import annotations

from datetime import datetime, timedelta

from ..models import FetchResult
from ..net import FetchError, PoliteClient
from ..normalize import build_item
from .base import BaseSource

ENDPOINT = "https://newsapi.org/v2/everything"


class NewsApiSource(BaseSource):
    type_name = "newsapi"

    def fetch(self, client: PoliteClient, fetched_at: datetime) -> FetchResult:
        api_key = self.require_env("api_key_env")
        params = {
            "q": str(self.option("query", "football OR soccer")),
            "language": str(self.option("language", "en")),
            "sortBy": "publishedAt",
            "pageSize": min(self.config.max_items, 100),
            "from": (fetched_at - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S"),
        }
        resp = client.get(
            ENDPOINT,
            params=params,
            headers={"X-Api-Key": api_key},  # header, so the key never appears in a URL
            timeout=self.config.timeout,
            check_robots=False,  # documented API; its terms govern access, not robots.txt
        )
        try:
            data = resp.json()
        except ValueError as exc:
            raise FetchError("NewsAPI returned non-JSON response") from exc
        if data.get("status") != "ok":
            raise FetchError(f"NewsAPI error: {data.get('code')}: {data.get('message')}")
        return self.parse(data, fetched_at)

    def parse(self, data: dict, fetched_at: datetime) -> FetchResult:
        result = FetchResult()
        for art in data.get("articles", [])[: self.config.max_items]:
            title = art.get("title") or ""
            if title.strip() == "[Removed]":
                result.invalid += 1
                continue
            publisher = (art.get("source") or {}).get("name") or "unknown"
            item = build_item(
                source=f"{publisher} (via {self.name})",
                title=title,
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
