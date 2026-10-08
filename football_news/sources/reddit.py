"""Reddit adapter using the official OAuth API (application-only flow). No HTML scraping."""

from __future__ import annotations

import os
from datetime import datetime

from ..models import FetchResult
from ..net import FetchError, PoliteClient
from ..normalize import build_item
from .base import BaseSource

TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
API_BASE = "https://oauth.reddit.com"


class RedditSource(BaseSource):
    type_name = "reddit"

    def fetch(self, client: PoliteClient, fetched_at: datetime) -> FetchResult:
        client_id = self.require_env("client_id_env")
        client_secret = self.require_env("client_secret_env")
        ua_env = self.option("user_agent_env")
        user_agent = (os.environ.get(str(ua_env)) if ua_env else None) or client.settings.user_agent
        headers = {"User-Agent": user_agent}

        token_resp = client.request(
            "POST",
            TOKEN_URL,
            data={"grant_type": "client_credentials"},
            auth=(client_id, client_secret),
            headers=headers,
            timeout=self.config.timeout,
            check_robots=False,
        )
        token = token_resp.json().get("access_token")
        if not token:
            raise FetchError("Reddit OAuth did not return an access token")

        subreddit = str(self.option("subreddit", "soccer"))
        listing = str(self.option("listing", "hot"))
        resp = client.get(
            f"{API_BASE}/r/{subreddit}/{listing}",
            params={"limit": min(self.config.max_items, 100), "raw_json": 1},
            headers={**headers, "Authorization": f"Bearer {token}"},
            timeout=self.config.timeout,
            check_robots=False,
        )
        return self.parse(resp.json(), fetched_at)

    def parse(self, data: dict, fetched_at: datetime) -> FetchResult:
        result = FetchResult()
        children = (data.get("data") or {}).get("children") or []
        for child in children[: self.config.max_items]:
            post = child.get("data") or {}
            if post.get("stickied"):
                continue  # daily discussion threads, not news
            permalink = f"https://www.reddit.com{post.get('permalink', '')}"
            link = post.get("url_overridden_by_dest") or permalink
            item = build_item(
                source=self.name,
                title=post.get("title"),
                link=link,
                summary=post.get("selftext"),
                published=post.get("created_utc"),
                fetched_at=fetched_at,
                max_summary_chars=self.summary_max_chars,
            )
            if item is None:
                result.invalid += 1
            else:
                result.items.append(item)
        return result
