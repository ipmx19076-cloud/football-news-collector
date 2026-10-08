"""End-to-end CLI tests with the network mocked out."""

import httpx
import pytest

from football_news import cli
from football_news.net import PoliteClient
from tests.conftest import FIXTURES

CONFIG = """
user_agent: test
network: {per_host_delay: 0, max_retries: 0}
storage: {db_path: "%(tmp)s/news.db", export_dir: "%(tmp)s/exports"}
logging: {dir: "%(tmp)s/logs"}
sources:
  - {name: Feed A, type: rss, url: "https://a.com/rss"}
  - {name: Feed B, type: rss, url: "https://b.com/rss"}
"""


@pytest.fixture
def config_path(tmp_path, monkeypatch):
    feed = (FIXTURES / "sample_rss.xml").read_bytes()

    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        if req.url.host == "b.com":
            return httpx.Response(503)
        return httpx.Response(200, content=feed)

    real_init = PoliteClient.__init__

    def fake_init(self, settings, **kw):
        real_init(self, settings, transport=httpx.MockTransport(handler), sleep=lambda s: None)

    monkeypatch.setattr(PoliteClient, "__init__", fake_init)
    path = tmp_path / "config.yaml"
    path.write_text(CONFIG % {"tmp": tmp_path.as_posix()}, encoding="utf-8")
    return path


def test_run_twice_is_idempotent(config_path, capsys):
    assert cli.main(["--config", str(config_path), "run"]) == 0
    first = capsys.readouterr().out
    assert cli.main(["--config", str(config_path), "run"]) == 0
    second = capsys.readouterr().out
    assert "items=3" in first and "items=3" in second
    assert "Feed B" in first and "failed" in first  # one source failing does not fail the run


def test_preview_does_not_create_db(config_path, tmp_path, capsys):
    assert cli.main(["--config", str(config_path), "preview", "--limit", "1"]) == 0
    assert "Arsenal edge Spurs" in capsys.readouterr().out
    assert not (tmp_path / "news.db").exists()


def test_bad_config_exits_2(tmp_path, capsys):
    assert cli.main(["--config", str(tmp_path / "missing.yaml"), "run"]) == 2
