import httpx
import pytest

from football_news.net import (
    FetchError, PoliteClient, RateLimitedError, RobotsDisallowedError, redact,
)

ROBOTS_OK = "User-agent: *\nDisallow: /private/\n"


def make_client(settings, handler):
    """Client with recorded sleeps and a clock that advances 100s per call (no host-delay waits)."""
    sleeps: list[float] = []
    ticks = iter(range(0, 1_000_000, 100))
    client = PoliteClient(settings, transport=httpx.MockTransport(handler),
                          sleep=sleeps.append, clock=lambda: float(next(ticks)))
    return client, sleeps


def router(responses):
    """responses: path -> Response, or a list of Responses consumed in order."""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        r = responses[request.url.path]
        return r.pop(0) if isinstance(r, list) else r

    return handler, calls


def test_success_sends_user_agent(net_settings):
    seen = {}

    def handler(req):
        seen[req.url.path] = req.headers["user-agent"]
        return httpx.Response(200, text=ROBOTS_OK if req.url.path == "/robots.txt" else "<rss/>")

    client, _ = make_client(net_settings, handler)
    assert client.get("https://e.com/feed").status_code == 200
    assert seen == {"/robots.txt": "test-agent/1.0", "/feed": "test-agent/1.0"}
    assert client.request_count == 2


def test_robots_disallow_blocks_fetch(net_settings):
    handler, calls = router({"/robots.txt": httpx.Response(200, text=ROBOTS_OK)})
    client, _ = make_client(net_settings, handler)
    with pytest.raises(RobotsDisallowedError):
        client.get("https://e.com/private/feed")
    assert calls == ["/robots.txt"]


def test_robots_404_means_allowed(net_settings):
    handler, _ = router({"/robots.txt": httpx.Response(404), "/feed": httpx.Response(200)})
    client, _ = make_client(net_settings, handler)
    assert client.get("https://e.com/feed").status_code == 200


def test_robots_5xx_is_treated_as_disallowed(net_settings):
    handler, calls = router({"/robots.txt": httpx.Response(503)})
    client, _ = make_client(net_settings, handler)
    with pytest.raises(RobotsDisallowedError):
        client.get("https://e.com/feed")
    assert calls == ["/robots.txt"]


def test_robots_cached_per_origin(net_settings):
    handler, calls = router({"/robots.txt": httpx.Response(200, text=ROBOTS_OK),
                             "/a": httpx.Response(200), "/b": httpx.Response(200)})
    client, _ = make_client(net_settings, handler)
    client.get("https://e.com/a")
    client.get("https://e.com/b")
    assert calls.count("/robots.txt") == 1


def test_403_fails_without_retry(net_settings):
    handler, calls = router({"/robots.txt": httpx.Response(404), "/feed": httpx.Response(403)})
    client, sleeps = make_client(net_settings, handler)
    with pytest.raises(FetchError, match="HTTP 403"):
        client.get("https://e.com/feed")
    assert calls.count("/feed") == 1 and sleeps == []


def test_5xx_retries_with_backoff_then_succeeds(net_settings):
    handler, _ = router({"/robots.txt": httpx.Response(404),
                         "/feed": [httpx.Response(503), httpx.Response(502), httpx.Response(200)]})
    client, sleeps = make_client(net_settings, handler)
    assert client.get("https://e.com/feed").status_code == 200
    assert sleeps == [2.0, 4.0]


def test_5xx_gives_up(net_settings):
    handler, _ = router({"/robots.txt": httpx.Response(404), "/feed": httpx.Response(500)})
    client, _ = make_client(net_settings, handler)
    with pytest.raises(FetchError, match="after 3 attempts"):
        client.get("https://e.com/feed")


def test_429_honours_retry_after_then_skips(net_settings):
    handler, calls = router({"/robots.txt": httpx.Response(404),
                             "/feed": httpx.Response(429, headers={"Retry-After": "5"})})
    client, sleeps = make_client(net_settings, handler)
    with pytest.raises(RateLimitedError):
        client.get("https://e.com/feed")
    assert sleeps == [5.0, 5.0] and calls.count("/feed") == 3


def test_429_long_retry_after_skips_immediately(net_settings):
    handler, calls = router({"/robots.txt": httpx.Response(404),
                             "/feed": httpx.Response(429, headers={"Retry-After": "3600"})})
    client, sleeps = make_client(net_settings, handler)
    with pytest.raises(RateLimitedError):
        client.get("https://e.com/feed")
    assert calls.count("/feed") == 1 and sleeps == []


def test_timeout_is_retried_then_fails(net_settings):
    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        raise httpx.ReadTimeout("slow", request=req)

    client, sleeps = make_client(net_settings, handler)
    with pytest.raises(FetchError, match="timeout"):
        client.get("https://e.com/feed")
    assert sleeps == [2.0, 4.0]


def test_per_host_delay_is_enforced(net_settings):
    sleeps: list[float] = []
    client = PoliteClient(net_settings, transport=httpx.MockTransport(lambda r: httpx.Response(200)),
                          sleep=sleeps.append, clock=lambda: 0.0)
    client.get("https://e.com/a", check_robots=False)
    client.get("https://e.com/b", check_robots=False)
    client.get("https://other.com/c", check_robots=False)
    assert sleeps == [1.0]


def test_redact_masks_keys():
    assert redact("https://gnews.io/api?q=x&apikey=SECRET123&max=1") == "https://gnews.io/api?q=x&apikey=***&max=1"
    assert "SECRET" not in redact("token=SECRET client_secret=SECRET")
