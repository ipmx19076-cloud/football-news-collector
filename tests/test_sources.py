import gzip
import json

import pytest

from football_news.config import ConfigError, SourceConfig
from football_news.net import FetchError
from football_news.sources import build_source
from football_news.sources.base import SourceSkipped
from tests.conftest import FETCHED_AT, FIXTURES


def rss(**options):
    return build_source(SourceConfig(name="Feed", type="rss", url="https://example.com/rss",
                                     options=options))


def test_rss_parses_and_drops_invalid(fixture_bytes):
    result = rss().parse(fixture_bytes("sample_rss.xml"), FETCHED_AT)
    assert result.invalid == 2
    first, second, third = result.items
    assert first.headline == "Arsenal edge Spurs in North London derby"
    assert first.url == "https://www.example.com/football/arsenal-spurs"
    assert first.published_at == "2026-10-08T13:30:00Z" and not first.date_inferred
    assert first.summary == "Arsenal beat Tottenham 2-1 & go top."
    assert second.date_inferred and second.published_at == "2026-10-08T15:00:00Z"
    assert third.summary.startswith("Ein klarer Sieg für")
    assert third.published_at == "2026-10-08T13:00:00Z"


def test_rss_handles_atom(fixture_bytes):
    (item,) = rss().parse(fixture_bytes("sample_atom.xml"), FETCHED_AT).items
    assert item.headline == "Juventus held by Inter" and item.summary == "Serie A summit ends level."


def test_rss_handles_gzip_body_without_header(fixture_bytes):
    result = rss().parse(gzip.compress(fixture_bytes("sample_atom.xml")), FETCHED_AT)
    assert len(result.items) == 1


def test_rss_malformed_raises(fixture_bytes):
    with pytest.raises(FetchError):
        rss().parse(fixture_bytes("not_a_feed.txt"), FETCHED_AT)


def test_rss_empty_feed_is_not_an_error():
    body = b'<?xml version="1.0"?><rss version="2.0"><channel><title>x</title></channel></rss>'
    result = rss().parse(body, FETCHED_AT)
    assert result.items == [] and "no entries" in result.notes[0]


def test_rss_assume_timezone(fixture_bytes):
    summer, winter = rss(assume_timezone="America/New_York").parse(
        fixture_bytes("espn_est.xml"), FETCHED_AT).items
    assert summer.published_at == "2026-10-08T12:51:09Z"
    assert winter.date_inferred  # Dec 2026 is "in the future" relative to FETCHED_AT


def test_rss_unknown_timezone_is_config_error():
    with pytest.raises(ConfigError):
        rss(assume_timezone="Mars/Olympus")


def test_max_items_respected(fixture_bytes):
    src = build_source(SourceConfig(name="F", type="rss", url="https://e.com", max_items=1))
    assert len(src.parse(fixture_bytes("sample_rss.xml"), FETCHED_AT).items) == 1


def test_newsapi_parse():
    src = build_source(SourceConfig(name="NewsAPI", type="newsapi", options={"api_key_env": "X"}))
    data = json.loads((FIXTURES / "newsapi.json").read_text(encoding="utf-8"))
    result = src.parse(data, FETCHED_AT)
    assert result.invalid == 2
    (item,) = result.items
    assert item.source == "Example Sport (via NewsAPI)"
    assert item.url == "https://example.net/man-utd" and item.summary == "Breaking news"


def test_api_source_without_key_is_skipped(monkeypatch):
    monkeypatch.delenv("NEWSAPI_KEY", raising=False)
    src = build_source(SourceConfig(name="NewsAPI", type="newsapi",
                                    options={"api_key_env": "NEWSAPI_KEY"}))
    with pytest.raises(SourceSkipped):
        src.fetch(client=None, fetched_at=FETCHED_AT)  # type: ignore[arg-type]


def test_unknown_source_type():
    with pytest.raises(ConfigError):
        build_source(SourceConfig(name="X", type="ftp"))
