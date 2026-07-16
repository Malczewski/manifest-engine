"""The `.bookpack` — the downloadable, self-contained unit the Android app reads.

Layout (zip):
    book.db            SQLite, schema below (SCHEMA_VERSION)
    images/scene_*.png generated scene images
    images/char_*.png  character reference images (optional)
    images/loc_*.png    location reference images (optional)

book.db schema:
    meta(key, value)                       -- book_id, title, author, base_prompt,
                                              schema_version, num_scenes, num_chapters,
                                              num_tokens
    chapters(idx, title, start_offset, end_offset, start_token, end_token)
    scenes(id, chapter_idx, seq, start_offset, end_offset, start_token, end_token,
           summary, location_id, characters, mood, time_of_day, image_path, prompt)
    tokens(pos, token, offset)             -- normalized stream; offset is char pos
    trigrams(gram, pos)                    -- inverted index for fuzzy matching
    entities(id, kind, name, descriptor, facts, image_path)

The app resolves the current token position from ASR, finds the scene whose
[start_token, end_token) contains it, and shows scenes.image_path.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import zipfile
from pathlib import Path

from .models import Chapter, Entity, Scene

SCHEMA_VERSION = 2

_PACK_SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE chapters (
    idx INTEGER PRIMARY KEY, title TEXT,
    start_offset INTEGER, end_offset INTEGER,
    start_token INTEGER, end_token INTEGER
);
CREATE TABLE scenes (
    id INTEGER PRIMARY KEY, chapter_idx INTEGER, seq INTEGER,
    start_offset INTEGER, end_offset INTEGER,
    start_token INTEGER, end_token INTEGER,
    summary TEXT, location_id TEXT, characters TEXT,
    mood TEXT, time_of_day TEXT, image_path TEXT, prompt TEXT
);
CREATE TABLE tokens (pos INTEGER PRIMARY KEY, token TEXT, offset INTEGER);
CREATE TABLE trigrams (gram TEXT, pos INTEGER);
CREATE INDEX idx_trigrams_gram ON trigrams(gram);
CREATE INDEX idx_scenes_tokens ON scenes(start_token, end_token);
CREATE TABLE entities (
    id TEXT PRIMARY KEY, kind TEXT, name TEXT, descriptor TEXT, facts TEXT, image_path TEXT
);
"""


class PackWriter:
    """Builds a pack directory (book.db + images/) then zips it to .bookpack."""

    def __init__(self, pack_dir: Path):
        self.dir = pack_dir
        self.images = pack_dir / "images"
        if pack_dir.exists():
            shutil.rmtree(pack_dir)
        self.images.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(pack_dir / "book.db")
        self.db.executescript(_PACK_SCHEMA)

    # -- image storage -------------------------------------------------------

    def save_image(self, rel_path: str, png: bytes) -> str:
        """rel_path like 'images/scene_0001.png'. Returns rel_path."""
        target = self.dir / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(png)
        return rel_path

    def read_image(self, rel_path: str) -> bytes:
        return (self.dir / rel_path).read_bytes()

    # -- table writers -------------------------------------------------------

    def write_meta(self, meta: dict[str, object]) -> None:
        self.db.executemany(
            "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
            [(k, str(v)) for k, v in meta.items()],
        )

    def write_chapters(self, chapters: list[Chapter]) -> None:
        self.db.executemany(
            "INSERT INTO chapters VALUES (?,?,?,?,?,?)",
            [
                (c.idx, c.title, c.start_offset, c.end_offset, c.start_token, c.end_token)
                for c in chapters
            ],
        )

    def write_scenes(self, scenes: list[Scene]) -> None:
        self.db.executemany(
            "INSERT INTO scenes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                (
                    s.id, s.chapter_idx, s.seq, s.start_offset, s.end_offset,
                    s.start_token, s.end_token, s.summary, s.location_id,
                    json.dumps(s.characters), s.mood, s.time_of_day, s.image_path,
                    s.prompt,
                )
                for s in scenes
            ],
        )

    def write_tokens(self, tokens: list[str], offsets: list[int]) -> None:
        self.db.executemany(
            "INSERT INTO tokens VALUES (?,?,?)",
            ((i, t, offsets[i]) for i, t in enumerate(tokens)),
        )

    def write_trigrams(self, grams: list[tuple[str, int]]) -> None:
        self.db.executemany("INSERT INTO trigrams VALUES (?,?)", grams)

    def write_entities(self, entities: list[Entity]) -> None:
        self.db.executemany(
            "INSERT INTO entities VALUES (?,?,?,?,?,?)",
            [
                (e.id, e.kind, e.name, e.descriptor, json.dumps(e.facts or []), e.image_path)
                for e in entities
            ],
        )

    # -- finalize ------------------------------------------------------------

    def finalize(self, out_path: Path) -> Path:
        self.db.commit()
        self.db.close()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if out_path.exists():
            out_path.unlink()
        with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in sorted(self.dir.rglob("*")):
                if f.is_file():
                    zf.write(f, f.relative_to(self.dir))
        return out_path
