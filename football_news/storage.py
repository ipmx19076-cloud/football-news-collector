"""SQLite storage: schema, migrations and queries."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from .models import NewsItem

# Each entry migrates the schema from version i to i+1. Append only; never edit old steps.
MIGRATIONS: list[str] = [
    """
    CREATE TABLE items (
        id             INTEGER PRIMARY KEY,
        url_key        TEXT NOT NULL UNIQUE,
        url            TEXT NOT NULL,
        headline       TEXT NOT NULL,
        headline_norm  TEXT NOT NULL,
        source         TEXT NOT NULL,
        published_at   TEXT NOT NULL,          -- ISO 8601 UTC, '...Z'
        date_inferred  INTEGER NOT NULL DEFAULT 0,
        summary        TEXT NOT NULL DEFAULT '',
        fetched_at     TEXT NOT NULL
    );
    CREATE INDEX idx_items_published ON items(published_at);
    CREATE INDEX idx_items_source ON items(source);

    -- Other sightings of a stored story: same story from another source or URL.
    CREATE TABLE duplicates (
        id          INTEGER PRIMARY KEY,
        item_id     INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
        url_key     TEXT NOT NULL UNIQUE,
        url         TEXT NOT NULL,
        source      TEXT NOT NULL,
        headline    TEXT NOT NULL,
        reason      TEXT NOT NULL,             -- 'headline'
        similarity  REAL,
        seen_at     TEXT NOT NULL
    );
    CREATE INDEX idx_duplicates_item ON duplicates(item_id);

    CREATE TABLE item_tags (
        item_id  INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
        kind     TEXT NOT NULL CHECK (kind IN ('league', 'club')),
        tag      TEXT NOT NULL,
        PRIMARY KEY (item_id, kind, tag)
    );
    CREATE INDEX idx_tags_tag ON item_tags(kind, tag);

    CREATE TABLE runs (
        id           INTEGER PRIMARY KEY,
        started_at   TEXT NOT NULL,
        finished_at  TEXT NOT NULL,
        new_items    INTEGER NOT NULL,
        duplicates   INTEGER NOT NULL,
        requests     INTEGER NOT NULL,
        report_json  TEXT NOT NULL
    );
    """,
    # v2: keep tag order so the first league (explicitly named in the text) is the primary one.
    """
    ALTER TABLE item_tags ADD COLUMN position INTEGER NOT NULL DEFAULT 0;
    CREATE INDEX idx_items_fetched ON items(fetched_at);
    """,
]


@dataclass(frozen=True, slots=True)
class Candidate:
    """A stored item that a new item may duplicate."""

    id: int
    source: str
    headline_norm: str


class Store:
    """Thin wrapper over a SQLite connection. Use as a context manager."""

    def __init__(self, path: Path | str) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.migrate()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @property
    def schema_version(self) -> int:
        return self.conn.execute("PRAGMA user_version").fetchone()[0]

    def migrate(self) -> None:
        version = self.schema_version
        for target, script in enumerate(MIGRATIONS[version:], start=version + 1):
            with self.conn:
                self.conn.executescript(f"BEGIN;\n{script}\nPRAGMA user_version = {target};\nCOMMIT;")

    @contextmanager
    def transaction(self) -> Iterator[None]:
        with self.conn:
            yield

    # -- dedup lookups ------------------------------------------------------

    def url_key_exists(self, url_key: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM items WHERE url_key = ? UNION ALL "
            "SELECT 1 FROM duplicates WHERE url_key = ? LIMIT 1",
            (url_key, url_key),
        ).fetchone()
        return row is not None

    def candidates(self, start_iso: str, end_iso: str) -> list[Candidate]:
        rows = self.conn.execute(
            "SELECT id, source, headline_norm FROM items WHERE published_at BETWEEN ? AND ?",
            (start_iso, end_iso),
        ).fetchall()
        return [Candidate(r["id"], r["source"], r["headline_norm"]) for r in rows]

    # -- writes -------------------------------------------------------------

    def insert_item(self, item: NewsItem, headline_norm: str) -> int:
        cur = self.conn.execute(
            """INSERT INTO items (url_key, url, headline, headline_norm, source, published_at,
                                  date_inferred, summary, fetched_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (item.url_key, item.url, item.headline, headline_norm, item.source,
             item.published_at, int(item.date_inferred), item.summary, item.fetched_at),
        )
        item_id = int(cur.lastrowid)
        self._write_tags(item_id, item.leagues, item.clubs)
        return item_id

    def _write_tags(self, item_id: int, leagues: tuple[str, ...], clubs: tuple[str, ...]) -> None:
        self.conn.executemany(
            "INSERT OR IGNORE INTO item_tags (item_id, kind, tag, position) VALUES (?, ?, ?, ?)",
            [(item_id, "league", t, i) for i, t in enumerate(leagues)]
            + [(item_id, "club", t, i) for i, t in enumerate(clubs)],
        )

    def replace_tags(self, item_id: int, leagues: tuple[str, ...], clubs: tuple[str, ...]) -> None:
        self.conn.execute("DELETE FROM item_tags WHERE item_id = ?", (item_id,))
        self._write_tags(item_id, leagues, clubs)

    def items_for_tagging(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT id, headline, summary FROM items").fetchall()

    def add_duplicate(self, item_id: int, item: NewsItem, reason: str, similarity: float | None) -> None:
        self.conn.execute(
            """INSERT OR IGNORE INTO duplicates (item_id, url_key, url, source, headline,
                                                 reason, similarity, seen_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (item_id, item.url_key, item.url, item.source, item.headline, reason,
             similarity, item.fetched_at),
        )

    def record_run(self, started_at: str, finished_at: str, new_items: int, duplicates: int,
                   requests: int, report: list[dict]) -> None:
        with self.conn:
            self.conn.execute(
                """INSERT INTO runs (started_at, finished_at, new_items, duplicates, requests, report_json)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (started_at, finished_at, new_items, duplicates, requests, json.dumps(report)),
            )

    # -- reads --------------------------------------------------------------

    def items(self, day: str | None = None, *, by: str = "published") -> list[dict]:
        """All items, or those whose `by` timestamp ('published' or 'fetched') falls on UTC day
        'YYYY-MM-DD'; newest first, with tags and 'also reported by' sources joined in.
        Tags are '|'-separated; leagues keep their priority order."""
        column = {"published": "published_at", "fetched": "fetched_at"}[by]
        where, params = (f"WHERE substr(i.{column}, 1, 10) = ?", (day,)) if day else ("", ())
        rows = self.conn.execute(
            f"""
            SELECT i.*,
                   (SELECT group_concat(tag, '|') FROM (SELECT tag FROM item_tags
                        WHERE item_id = i.id AND kind = 'league' ORDER BY position)) AS leagues,
                   (SELECT group_concat(tag, '|') FROM (SELECT tag FROM item_tags
                        WHERE item_id = i.id AND kind = 'club' ORDER BY position)) AS clubs,
                   (SELECT group_concat(source, '|') FROM (SELECT DISTINCT source FROM duplicates
                        WHERE item_id = i.id AND source != i.source ORDER BY source)) AS also_reported_by
            FROM items i {where}
            ORDER BY i.published_at DESC, i.id DESC
            """,
            params,
        ).fetchall()
        return [dict(r) for r in rows]

    def count(self, table: str) -> int:
        if table not in {"items", "duplicates", "item_tags", "runs"}:
            raise ValueError(table)
        return self.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
