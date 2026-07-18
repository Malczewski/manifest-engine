"""Server-side catalog/job database (SQLite).

This is the server's own bookkeeping DB. It is NOT the `.bookpack` shipped to
the app — that is a separate per-book SQLite file assembled in bookpack.py.
"""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from typing import Iterator

from .config import settings

_lock = threading.Lock()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS books (
    id          TEXT PRIMARY KEY,
    title       TEXT NOT NULL DEFAULT '',
    author      TEXT NOT NULL DEFAULT '',
    base_prompt TEXT NOT NULL DEFAULT '',
    extra_prompt TEXT NOT NULL DEFAULT '',
    epub_path   TEXT NOT NULL DEFAULT '',
    pack_path   TEXT NOT NULL DEFAULT '',
    num_scenes  INTEGER NOT NULL DEFAULT 0,
    status      TEXT NOT NULL DEFAULT 'queued',
    stage       TEXT NOT NULL DEFAULT '',
    progress    REAL NOT NULL DEFAULT 0.0,
    message     TEXT NOT NULL DEFAULT '',
    error       TEXT,
    series_id   TEXT NOT NULL DEFAULT '',
    series_seq  INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS series (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL DEFAULT '',
    world      TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Shared story bible across all books in a series: recurring characters/locations
-- keep the same description (and, when generated, reference image), which under
-- Approach A also yields the same seed -> visual consistency across books.
CREATE TABLE IF NOT EXISTS series_entities (
    series_id  TEXT NOT NULL,
    norm       TEXT NOT NULL,          -- normalize_name() key
    kind       TEXT NOT NULL,
    name       TEXT NOT NULL,
    descriptor TEXT NOT NULL DEFAULT '',
    facts      TEXT NOT NULL DEFAULT '[]',  -- JSON list of accumulated stable facts
    image_path TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (series_id, norm)
);
"""

# Columns added after the initial release; applied to pre-existing DBs.
_MIGRATIONS = [
    ("books", "series_id", "TEXT NOT NULL DEFAULT ''"),
    ("books", "series_seq", "INTEGER NOT NULL DEFAULT 0"),
    ("series_entities", "facts", "TEXT NOT NULL DEFAULT '[]'"),
    ("books", "extra_prompt", "TEXT NOT NULL DEFAULT ''"),
]


def init_db() -> None:
    settings.ensure_dirs()
    with connect() as conn:
        conn.executescript(_SCHEMA)
        for table, column, decl in _MIGRATIONS:
            cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
            if column not in cols:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    """A short-lived connection. Serialized with a process lock because the
    single-user server has very low write concurrency and this keeps SQLite
    simple and correct across the API + background job threads."""
    with _lock:
        conn = sqlite3.connect(settings.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()
