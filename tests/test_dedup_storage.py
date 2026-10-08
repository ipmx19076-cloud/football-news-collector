from dataclasses import replace

import pytest

from football_news.dedup import Deduplicator, Outcome, normalize_headline, similarity
from football_news.models import NewsItem
from football_news.normalize import url_key
from football_news.storage import MIGRATIONS, Store


def item(headline: str, source: str, url: str, published: str = "2026-10-08T10:00:00Z") -> NewsItem:
    return NewsItem(headline=headline, source=source, url=url, url_key=url_key(url),
                    published_at=published, date_inferred=False, summary="s",
                    fetched_at="2026-10-08T15:00:00Z")


@pytest.fixture
def store():
    with Store(":memory:") as s:
        yield s


@pytest.fixture
def dedup(store):
    return Deduplicator(store)


def test_schema_created_and_migrations_idempotent(tmp_path):
    path = tmp_path / "x.db"
    with Store(path) as s:
        assert s.schema_version == len(MIGRATIONS)
    with Store(path) as s:  # reopening must not re-run migrations
        assert s.schema_version == len(MIGRATIONS) and s.count("items") == 0


def test_normalize_headline():
    assert normalize_headline("Mbappé's late goal: Real Madrid beat Barça!") == "mbappe late goal real madrid beat barca"
    assert normalize_headline("Arsenal sign Rice - BBC Sport", "BBC Sport") == "arsenal sign rice"
    assert normalize_headline("Mbappe’s goal") == normalize_headline("Mbappe goal")
    assert normalize_headline("Slot hails 'sensational' Salah") == "slot hail sensational salah"
    # a subtitle that is not the publisher is kept
    assert normalize_headline("Arsenal news - Saka injury", "BBC Sport") == "arsenal news saka injury"
    # publisher name without a separator is part of the headline
    assert normalize_headline("Why I love BBC Sport", "BBC Sport") == "i love bbc sport"


def test_same_transfer_story_on_bbc_and_sky_stored_once(store, dedup):
    bbc = item("Chelsea complete £60m signing of Joao Felix from Atletico Madrid",
               "BBC Sport Football", "https://www.bbc.co.uk/sport/football/articles/abc?at_medium=RSS")
    sky = item("Chelsea complete £60m signing of João Félix from Atlético Madrid",
               "Sky Sports Football", "https://www.skysports.com/football/news/11095/999/chelsea-felix",
               published="2026-10-08T11:30:00Z")
    assert dedup.add(bbc) is Outcome.NEW
    assert dedup.add(sky) is Outcome.DUPLICATE_STORY
    (row,) = store.items()
    assert row["source"] == "BBC Sport Football"
    assert row["also_reported_by"] == "Sky Sports Football"
    assert store.count("duplicates") == 1


def test_reworded_headline_caught_by_jaccard(dedup):
    a = item("Arsenal confirm Saka hamstring injury ahead of derby", "A", "https://a.com/1")
    b = item("Saka hamstring injury confirmed by Arsenal ahead of derby", "B", "https://b.com/2")
    j, _ = similarity(normalize_headline(a.headline), normalize_headline(b.headline))
    assert j >= 0.8
    assert dedup.add(a) is Outcome.NEW
    assert dedup.add(b) is Outcome.DUPLICATE_STORY


def test_different_stories_are_kept(dedup):
    a = item("Arsenal v Tottenham: Premier League live", "A", "https://a.com/1")
    b = item("Chelsea v Liverpool: Premier League live", "B", "https://b.com/2")
    assert dedup.add(a) is Outcome.NEW
    assert dedup.add(b) is Outcome.NEW


def test_same_source_similar_headline_not_merged(dedup):
    a = item("Premier League predictions for gameweek eight", "A", "https://a.com/gw8")
    b = item("Premier League predictions for gameweek eight", "A", "https://a.com/gw8-updated")
    assert dedup.add(a) is Outcome.NEW
    assert dedup.add(b) is Outcome.NEW


def test_outside_time_window_not_merged(dedup):
    a = item("Premier League fixtures announced for new season", "A", "https://a.com/1",
             published="2026-06-01T10:00:00Z")
    b = item("Premier League fixtures announced for new season", "B", "https://b.com/1",
             published="2026-06-05T10:00:00Z")
    assert dedup.add(a) is Outcome.NEW
    assert dedup.add(b) is Outcome.NEW


def test_short_headlines_need_exact_match(dedup):
    assert dedup.add(item("Transfer news live", "A", "https://a.com/1")) is Outcome.NEW
    assert dedup.add(item("Transfer news: live!", "B", "https://b.com/1")) is Outcome.DUPLICATE_STORY
    assert dedup.add(item("Transfer news today", "C", "https://c.com/1")) is Outcome.NEW


def test_url_variants_are_the_same_item(dedup, store):
    base = item("Some headline about football", "A", "https://www.example.com/story")
    assert dedup.add(base) is Outcome.NEW
    for url in ("http://example.com/story/", "https://example.com/story?utm_source=x#c"):
        assert dedup.add(replace(base, url=url, url_key=url_key(url))) is Outcome.DUPLICATE_URL
    assert store.count("items") == 1 and store.count("duplicates") == 0


def test_rerun_is_idempotent(dedup, store):
    batch = [
        item("Chelsea complete signing of Joao Felix", "BBC", "https://bbc.co.uk/1"),
        item("Chelsea complete signing of João Félix", "Sky", "https://skysports.com/1"),
        item("Bayern rout Dortmund in Der Klassiker", "BBC", "https://bbc.co.uk/2"),
    ]
    first = [dedup.add(i) for i in batch]
    second = [dedup.add(i) for i in batch]
    assert first == [Outcome.NEW, Outcome.DUPLICATE_STORY, Outcome.NEW]
    assert second == [Outcome.DUPLICATE_URL] * 3
    assert store.count("items") == 2 and store.count("duplicates") == 1


def test_items_filter_by_day_and_tags(store):
    tagged = replace(item("Arsenal edge Spurs", "A", "https://a.com/1"),
                     leagues=("Premier League",), clubs=("Tottenham", "Arsenal"))
    store.insert_item(tagged, "arsenal edge spurs")
    store.insert_item(item("Old story", "A", "https://a.com/2", published="2026-10-01T10:00:00Z"), "old story")
    (row,) = store.items("2026-10-08")
    assert row["leagues"] == "Premier League" and row["clubs"] == "Tottenham|Arsenal"  # order kept
    assert len(store.items()) == 2
