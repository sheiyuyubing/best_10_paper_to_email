from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .models import Paper


class PaperStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def initialize(self) -> None:
        with closing(self.connect()) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS papers (
                    key TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    authors TEXT NOT NULL,
                    venue TEXT NOT NULL,
                    venue_type TEXT NOT NULL,
                    category TEXT NOT NULL,
                    publication_date TEXT,
                    date_is_exact INTEGER NOT NULL DEFAULT 1,
                    doi TEXT,
                    url TEXT,
                    abstract TEXT,
                    sources TEXT NOT NULL,
                    score REAL NOT NULL DEFAULT 0,
                    first_seen_at TEXT NOT NULL,
                    sent_at TEXT
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            connection.commit()

    def get_metadata(self, key: str) -> str | None:
        with closing(self.connect()) as connection:
            row = connection.execute(
                "SELECT value FROM metadata WHERE key = ?", (key,)
            ).fetchone()
        return str(row["value"]) if row else None

    def set_metadata(self, key: str, value: str) -> None:
        with closing(self.connect()) as connection:
            connection.execute(
                """
                INSERT INTO metadata (key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, value),
            )
            connection.commit()

    def last_success_at(self) -> datetime | None:
        value = self.get_metadata("last_success_at")
        if not value:
            return None
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None

    def mark_success(self) -> None:
        self.set_metadata("last_success_at", datetime.now(timezone.utc).isoformat())

    def sent_keys(self) -> set[str]:
        with closing(self.connect()) as connection:
            rows = connection.execute(
                "SELECT key FROM papers WHERE sent_at IS NOT NULL"
            ).fetchall()
        return {str(row["key"]) for row in rows}

    def save(self, papers: Iterable[Paper]) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with closing(self.connect()) as connection:
            for paper in papers:
                connection.execute(
                    """
                    INSERT INTO papers (
                        key, title, authors, venue, venue_type, category,
                        publication_date, date_is_exact, doi, url, abstract,
                        sources, score, first_seen_at, sent_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                    ON CONFLICT(key) DO UPDATE SET
                        title = excluded.title,
                        authors = excluded.authors,
                        venue = excluded.venue,
                        venue_type = excluded.venue_type,
                        category = excluded.category,
                        publication_date = excluded.publication_date,
                        date_is_exact = excluded.date_is_exact,
                        doi = excluded.doi,
                        url = excluded.url,
                        abstract = excluded.abstract,
                        sources = excluded.sources,
                        score = excluded.score
                    """,
                    (
                        paper.key,
                        paper.title,
                        json.dumps(paper.authors, ensure_ascii=False),
                        paper.venue,
                        paper.venue_type,
                        paper.category,
                        paper.publication_date.isoformat() if paper.publication_date else None,
                        int(paper.date_is_exact),
                        paper.doi,
                        paper.url,
                        paper.abstract,
                        json.dumps(sorted(paper.sources), ensure_ascii=False),
                        paper.score,
                        now,
                    ),
                )
            connection.commit()

    def mark_sent(self, keys: Iterable[str]) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with closing(self.connect()) as connection:
            connection.executemany(
                "UPDATE papers SET sent_at = ? WHERE key = ?",
                [(now, key) for key in keys],
            )
            connection.commit()

