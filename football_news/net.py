"""Polite HTTP client: User-Agent, timeouts, retries with backoff, per-host delay, robots.txt."""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from .config import NetworkSettings

log = logging.getLogger(__name__)

_SECRET_PARAM = re.compile(r"(?i)((?:api_?key|apikey|token|access_token|client_secret)=)[^&\s]+")


class FetchError(Exception):
    """A request failed in a way that should skip this source for this run."""


class RateLimitedError(FetchError):
    """HTTP 429 persisted after backing off; skip the source for the day."""


class RobotsDisallowedError(FetchError):
    """robots.txt disallows (or could not confirm) automated access to this URL."""


def redact(text: str) -> str:
    """Mask secret-looking query parameters so they never reach logs or output."""
    return _SECRET_PARAM.sub(r"\1***", text)


def _retry_after_seconds(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        return max(0.0, parsedate_to_datetime(value).timestamp() - time.time())
    except (TypeError, ValueError):
        return None


class PoliteClient:
    """Wraps httpx.Client. One instance per run so per-host timing and robots cache are shared."""

    def __init__(
        self,
        settings: NetworkSettings,
        *,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.settings = settings
        self._client = httpx.Client(
            headers={"User-Agent": settings.user_agent},
            follow_redirects=True,
            transport=transport,
        )
        self._sleep = sleep
        self._clock = clock
        self._last_request: dict[str, float] = {}
        self._robots: dict[str, RobotFileParser | None] = {}
        self.request_count = 0

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> PoliteClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- politeness helpers -------------------------------------------------

    def _wait_for_host(self, host: str) -> None:
        last = self._last_request.get(host)
        if last is not None:
            remaining = self.settings.per_host_delay - (self._clock() - last)
            if remaining > 0:
                self._sleep(remaining)
        self._last_request[host] = self._clock()

    def _robots_for(self, url: str) -> RobotFileParser | None:
        """Fetch and cache robots.txt per origin. None means 'could not be retrieved'."""
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin in self._robots:
            return self._robots[origin]
        parser = RobotFileParser()
        robots_url = f"{origin}/robots.txt"
        try:
            self._wait_for_host(parts.netloc)
            self.request_count += 1
            resp = self._client.get(robots_url, timeout=self.settings.default_timeout)
        except httpx.HTTPError as exc:
            log.warning("robots.txt unreachable for %s (%s)", origin, type(exc).__name__)
            self._robots[origin] = None
            return None
        if resp.status_code in (401, 403):
            parser.disallow_all = True
        elif 400 <= resp.status_code < 500:
            parser.allow_all = True  # no robots.txt: no restrictions declared
        elif resp.status_code >= 500:
            log.warning("robots.txt for %s returned HTTP %s", origin, resp.status_code)
            self._robots[origin] = None
            return None
        else:
            parser.parse(resp.text.splitlines())
        self._robots[origin] = parser
        return parser

    def check_robots(self, url: str) -> None:
        """Raise RobotsDisallowedError unless robots.txt permits fetching url."""
        if not self.settings.respect_robots:
            return
        parser = self._robots_for(url)
        if parser is None:
            raise RobotsDisallowedError(
                f"could not read robots.txt for {urlsplit(url).netloc}; not fetching to be safe"
            )
        if not parser.can_fetch(self.settings.user_agent, url):
            raise RobotsDisallowedError(f"robots.txt disallows {redact(url)}")

    # -- requests -----------------------------------------------------------

    def request(
        self,
        method: str,
        url: str,
        *,
        timeout: float | None = None,
        check_robots: bool = True,
        **kwargs: object,
    ) -> httpx.Response:
        """Send a request with retries. Raises FetchError (or a subclass) on failure."""
        if check_robots:
            self.check_robots(url)
        host = urlsplit(url).netloc
        timeout = timeout or self.settings.default_timeout
        attempts = self.settings.max_retries + 1
        safe_url = redact(url)

        for attempt in range(attempts):
            last_attempt = attempt == attempts - 1
            backoff = self.settings.backoff_base ** (attempt + 1)
            self._wait_for_host(host)
            self.request_count += 1
            try:
                resp = self._client.request(method, url, timeout=timeout, **kwargs)
            except httpx.TimeoutException as exc:
                reason = f"timeout after {timeout:g}s ({type(exc).__name__})"
            except httpx.HTTPError as exc:
                reason = f"network error ({type(exc).__name__})"
            else:
                status = resp.status_code
                if status == 429:
                    wait = _retry_after_seconds(resp.headers.get("Retry-After"))
                    wait = backoff if wait is None else wait
                    if last_attempt or wait > self.settings.max_retry_after:
                        raise RateLimitedError(
                            f"HTTP 429 rate limited by {host}; skipping for today"
                        )
                    log.warning("%s: HTTP 429, backing off %.1fs", host, wait)
                    self._sleep(wait)
                    continue
                if status >= 500:
                    reason = f"HTTP {status}"
                elif status >= 400:
                    raise FetchError(f"HTTP {status} from {safe_url}")
                else:
                    return resp
            if last_attempt:
                raise FetchError(f"{reason} from {safe_url} after {attempts} attempts")
            log.warning("%s: %s, retrying in %.1fs", host, reason, backoff)
            self._sleep(backoff)
        raise FetchError(f"request to {safe_url} failed")  # unreachable; keeps type checkers happy

    def get(self, url: str, **kwargs: object) -> httpx.Response:
        return self.request("GET", url, **kwargs)
