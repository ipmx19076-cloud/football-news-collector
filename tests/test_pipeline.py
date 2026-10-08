import logging

import httpx

from football_news.config import Config, NetworkSettings, SourceConfig
from football_news.net import PoliteClient
from football_news.pipeline import collect
from tests.conftest import FETCHED_AT, FIXTURES


def make_config(sources):
    return Config(network=NetworkSettings(user_agent="t", per_host_delay=0, max_retries=0),
                  sources=sources, db_path=None, export_dir=None, log_dir=None,  # type: ignore[arg-type]
                  log_level="INFO", summary_max_chars=280, raw={})


def test_one_failing_source_does_not_stop_others(monkeypatch, caplog):
    monkeypatch.delenv("NEWSAPI_KEY", raising=False)
    feed = (FIXTURES / "sample_atom.xml").read_bytes()

    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        if req.url.host == "bad.com":
            return httpx.Response(403)
        return httpx.Response(200, content=feed)

    config = make_config([
        SourceConfig(name="Bad", type="rss", url="https://bad.com/rss"),
        SourceConfig(name="Good", type="rss", url="https://good.com/rss"),
        SourceConfig(name="Disabled", type="rss", url="https://good.com/x", enabled=False),
        SourceConfig(name="NewsAPI", type="newsapi", options={"api_key_env": "NEWSAPI_KEY"}),
    ])
    client = PoliteClient(config.network, transport=httpx.MockTransport(handler), sleep=lambda s: None)
    with caplog.at_level(logging.INFO):
        reports = {r.name: r for r in collect(config, client, FETCHED_AT)}

    assert set(reports) == {"Bad", "Good", "NewsAPI"}
    assert reports["Bad"].status == "failed" and "403" in reports["Bad"].message
    assert reports["Good"].status == "ok" and reports["Good"].fetched == 1
    assert reports["NewsAPI"].status == "skipped"
    assert any(r.levelname == "WARNING" and "Bad" in r.getMessage() for r in caplog.records)
    assert any(r.levelname == "INFO" and "NEWSAPI_KEY" in r.getMessage() for r in caplog.records)


def test_adapter_bug_is_contained(monkeypatch):
    from football_news.sources.rss import RssSource

    def boom(self, client, fetched_at):
        raise KeyError("oops")

    monkeypatch.setattr(RssSource, "fetch", boom)
    config = make_config([SourceConfig(name="A", type="rss", url="https://a.com")])
    (report,) = collect(config, PoliteClient(config.network), FETCHED_AT)
    assert report.status == "failed" and "KeyError" in report.message
