from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from football_news.config import NetworkSettings

FIXTURES = Path(__file__).parent / "fixtures"
FETCHED_AT = datetime(2026, 10, 8, 15, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def fixture_bytes():
    return lambda name: (FIXTURES / name).read_bytes()


@pytest.fixture
def net_settings() -> NetworkSettings:
    return NetworkSettings(
        user_agent="test-agent/1.0", default_timeout=5, max_retries=2,
        backoff_base=2.0, per_host_delay=1.0, max_retry_after=30, respect_robots=True,
    )
