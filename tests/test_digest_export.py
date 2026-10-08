import csv
import sqlite3
from dataclasses import replace

from football_news.digest import md_escape, render_digest, write_digest
from football_news.models import NewsItem
from football_news.normalize import url_key
from football_news.storage import MIGRATIONS, Store

ORDER = ["Premier League", "Champions League", "Other/General"]


def item(headline, source, url, published, leagues=(), clubs=(), inferred=False):
    return NewsItem(headline=headline, source=source, url=url, url_key=url_key(url),
                    published_at=published, date_inferred=inferred, summary="",
                    fetched_at="2026-10-08T15:00:00Z", leagues=leagues, clubs=clubs)


def populated_store():
    store = Store(":memory:")
    ids = [
        store.insert_item(item("Arsenal edge Spurs", "BBC", "https://bbc.co.uk/1", "2026-10-08T13:30:00Z",
                               ("Premier League",), ("Arsenal", "Tottenham")), "x"),
        store.insert_item(item("Arsenal beat PSG [report]", "Sky", "https://sky.com/2", "2026-10-08T09:00:00Z",
                               ("Champions League", "Premier League"), ("Arsenal",)), "y"),
        store.insert_item(item("FA review", "Guardian", "https://g.com/3", "2026-10-08T15:00:00Z",
                               ("Other/General",), (), inferred=True), "z"),
        store.insert_item(item("Late-dated old story", "BBC", "https://bbc.co.uk/4", "2026-10-06T08:00:00Z",
                               ("Premier League",)), "w"),
    ]
    store.add_duplicate(ids[0], item("Arsenal edge Spurs!", "Sky", "https://sky.com/9",
                                     "2026-10-08T13:40:00Z"), "headline", 0.95)
    return store


def test_digest_groups_by_primary_league_and_links():
    store = populated_store()
    md = render_digest(store.items("2026-10-08", by="fetched"), "2026-10-08", ORDER, "Other/General")
    pl, ucl, other = md.index("## Premier League (2)"), md.index("## Champions League (1)"), md.index("## Other/General (1)")
    assert pl < ucl < other
    assert "- `13:30` [Arsenal edge Spurs](https://bbc.co.uk/1) (BBC) · also: Sky · Arsenal, Tottenham" in md
    assert "[Arsenal beat PSG \\[report\\]](https://sky.com/2) (Sky) · also in Premier League · Arsenal" in md
    assert "`time unknown` [FA review]" in md
    assert "`10-06 08:00` [Late-dated old story]" in md  # collected today, published earlier
    assert md.count("Arsenal beat PSG") == 1  # listed once, under its primary league
    assert "[Premier League (2)](#premier-league-2)" in md


def test_empty_day_digest(tmp_path):
    path = write_digest(Store(":memory:"), "2026-01-01", tmp_path, ORDER, "Other/General")
    assert "No new stories" in path.read_text(encoding="utf-8")


def test_digest_rewrite_is_idempotent(tmp_path):
    store = populated_store()
    a = write_digest(store, "2026-10-08", tmp_path, ORDER, "Other/General").read_text(encoding="utf-8")
    b = write_digest(store, "2026-10-08", tmp_path, ORDER, "Other/General").read_text(encoding="utf-8")
    strip = lambda s: "\n".join(line for line in s.splitlines() if "Generated" not in line)  # noqa: E731
    assert strip(a) == strip(b) and len(list(tmp_path.iterdir())) == 1


def test_md_escape():
    assert md_escape("a*b_[c]") == "a\\*b\\_\\[c\\]"


def test_migration_from_v1_keeps_data(tmp_path):
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.executescript(MIGRATIONS[0] + "\nPRAGMA user_version = 1;")
    conn.execute("INSERT INTO items (url_key, url, headline, headline_norm, source, published_at, fetched_at) "
                 "VALUES ('k', 'u', 'h', 'h', 's', '2026-10-08T00:00:00Z', '2026-10-08T00:00:00Z')")
    conn.execute("INSERT INTO item_tags (item_id, kind, tag) VALUES (1, 'league', 'Serie A')")
    conn.commit()
    conn.close()
    with Store(path) as store:
        assert store.schema_version == len(MIGRATIONS) == 2
        assert store.items()[0]["leagues"] == "Serie A"


def test_export_csv(tmp_path, monkeypatch):
    from football_news import cli
    store = populated_store()
    monkeypatch.setattr(cli, "Store", lambda path: store)
    store.close = lambda: None  # keep the in-memory db alive across the context manager
    store.insert_item(replace(item("=HYPERLINK(evil)", "X", "https://x.com/5", "2026-10-08T01:00:00Z")), "q")

    class Cfg:
        db_path = "unused"
        export_dir = tmp_path

    args = type("A", (), {"date": "2026-10-08", "out": None})()
    assert cli.cmd_export(Cfg, None, args) == 0  # type: ignore[arg-type]
    with (tmp_path / "football_news_2026-10-08.csv").open(encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 4  # the 2026-10-06 item is excluded by published date
    first = rows[0]
    assert list(first)[:4] == ["id", "published_at", "date_inferred", "headline"]
    by_headline = {r["headline"]: r for r in rows}
    assert by_headline["Arsenal edge Spurs"]["also_reported_by"] == "Sky"
    assert by_headline["Arsenal edge Spurs"]["clubs"] == "Arsenal|Tottenham"
    assert "'=HYPERLINK(evil)" in by_headline
