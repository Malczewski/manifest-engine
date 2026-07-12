"""Series catalog + shared story bible persistence.

A series owns a `world` string and a set of entities (the bible). Processing a
book in a series loads this bible so recurring characters keep their description
(and reference image), then writes back any new/updated entities so later books
inherit them.
"""

from __future__ import annotations

import uuid

from .db import connect
from .models import Entity


def create_series(name: str) -> str:
    series_id = uuid.uuid4().hex[:12]
    with connect() as conn:
        conn.execute("INSERT INTO series(id, name) VALUES (?, ?)", (series_id, name or "Untitled series"))
    return series_id


def list_series() -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT s.id, s.name, COUNT(b.id) AS book_count
            FROM series s LEFT JOIN books b ON b.series_id = s.id
            GROUP BY s.id ORDER BY s.created_at DESC
            """
        ).fetchall()
    return [dict(r) for r in rows]


def next_seq(series_id: str) -> int:
    with connect() as conn:
        row = conn.execute(
            "SELECT COALESCE(MAX(series_seq), 0) + 1 AS n FROM books WHERE series_id = ?",
            (series_id,),
        ).fetchone()
    return int(row["n"])


def load_bible(series_id: str) -> tuple[str, dict[str, dict]]:
    """Return (world, {norm: {kind, name, descriptor, image_path}})."""
    if not series_id:
        return "", {}
    with connect() as conn:
        srow = conn.execute("SELECT world FROM series WHERE id = ?", (series_id,)).fetchone()
        world = srow["world"] if srow else ""
        rows = conn.execute(
            "SELECT norm, kind, name, descriptor, image_path FROM series_entities WHERE series_id = ?",
            (series_id,),
        ).fetchall()
    return world, {r["norm"]: dict(r) for r in rows}


def save_bible(series_id: str, world: str, entities: list[Entity]) -> None:
    """Upsert the series world + entities after a book is processed."""
    if not series_id:
        return
    from .pipeline.bible import normalize_name

    with connect() as conn:
        if world:
            conn.execute(
                "UPDATE series SET world = ? WHERE id = ? AND (world = '' OR ? <> '')",
                (world, series_id, world),
            )
        for e in entities:
            norm = normalize_name(e.name)
            # Keep the first non-empty descriptor/image we learn for an entity so
            # later books stay consistent with earlier ones.
            conn.execute(
                """
                INSERT INTO series_entities(series_id, norm, kind, name, descriptor, image_path)
                VALUES (?,?,?,?,?,?)
                ON CONFLICT(series_id, norm) DO UPDATE SET
                    descriptor = CASE WHEN series_entities.descriptor = '' THEN excluded.descriptor
                                      ELSE series_entities.descriptor END,
                    image_path = CASE WHEN series_entities.image_path = '' THEN excluded.image_path
                                      ELSE series_entities.image_path END
                """,
                (series_id, norm, e.kind, e.name, e.descriptor, e.image_path),
            )
