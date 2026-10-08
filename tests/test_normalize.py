from datetime import datetime, timezone

from dateutil import tz

from football_news.normalize import (
    build_item, clean_url, parse_datetime, strip_html, to_iso, truncate, url_key,
)
from tests.conftest import FETCHED_AT


def test_strip_html_removes_tags_scripts_and_entities():
    assert strip_html("<p>Hello &amp; <b>welcome</b></p><script>x()</script>") == "Hello & welcome"
    assert strip_html(None) == ""
    assert strip_html("<p>unclosed <b>tag") == "unclosed tag"


def test_truncate_on_word_boundary():
    out = truncate(("word " * 100).strip(), 50)
    assert len(out) <= 50 and out.endswith("…") and not out.endswith(" …")
    assert truncate("short", 50) == "short"


def test_clean_url_strips_tracking_but_keeps_real_params():
    url = "https://www.bbc.co.uk/sport/x?at_medium=RSS&at_campaign=rss&id=7&utm_source=t#frag"
    assert clean_url(url) == "https://www.bbc.co.uk/sport/x?id=7"


def test_url_key_normalizes_scheme_host_slash_and_amp():
    a = url_key("http://WWW.Example.com/story/?utm_source=x")
    b = url_key("https://example.com/story")
    c = url_key("https://amp.example.com/story/amp")
    assert a == b == c == "https://example.com/story"


def test_parse_datetime_variants():
    utc = timezone.utc
    assert parse_datetime("Wed, 08 Oct 2026 14:30:00 +0100") == datetime(2026, 10, 8, 13, 30, tzinfo=utc)
    assert parse_datetime("Wed, 08 Oct 2026 14:30:00 BST") == datetime(2026, 10, 8, 13, 30, tzinfo=utc)
    assert parse_datetime("2026-10-08T09:00:00-04:00") == datetime(2026, 10, 8, 13, 0, tzinfo=utc)
    assert parse_datetime("2026-10-08 09:00") == datetime(2026, 10, 8, 9, 0, tzinfo=utc)  # naive -> UTC
    assert parse_datetime(1_791_464_400) is not None
    assert parse_datetime("not a date") is None
    assert parse_datetime("") is None


def test_assume_timezone_overrides_wrong_label():
    ny = tz.gettz("America/New_York")
    # "EST" in October is really EDT (UTC-4)
    assert parse_datetime("Thu, 8 Oct 2026 08:51:09 EST", ny) == datetime(2026, 10, 8, 12, 51, 9, tzinfo=timezone.utc)
    # in December it really is EST (UTC-5)
    assert parse_datetime("Tue, 8 Dec 2026 08:51:09 EST", ny) == datetime(2026, 12, 8, 13, 51, 9, tzinfo=timezone.utc)


def test_to_iso_is_utc_z():
    assert to_iso(datetime(2026, 10, 8, 14, 30, 5, 123, tzinfo=tz.gettz("Europe/London"))) == "2026-10-08T13:30:05Z"


def test_build_item_missing_date_is_inferred():
    item = build_item(source="S", title="T", link="https://x.com/a", summary=None,
                      published=None, fetched_at=FETCHED_AT)
    assert item.date_inferred and item.published_at == "2026-10-08T15:00:00Z" == item.fetched_at


def test_build_item_rejects_missing_title_or_bad_link():
    kw = dict(source="S", summary=None, published=None, fetched_at=FETCHED_AT)
    assert build_item(title="", link="https://x.com", **kw) is None
    assert build_item(title="T", link=None, **kw) is None
    assert build_item(title="T", link="javascript:alert(1)", **kw) is None


def test_build_item_future_date_treated_as_missing():
    item = build_item(source="S", title="T", link="https://x.com/a", summary=None,
                      published="2030-01-01T00:00:00Z", fetched_at=FETCHED_AT)
    assert item.date_inferred
