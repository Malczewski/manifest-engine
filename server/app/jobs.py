"""Background job runner for the pre-processing pipeline.

Single-user local server: one worker thread per job is plenty. The pipeline is
synchronous (ebooklib, Pillow, blocking HTTP to the image backend), so it runs
in a thread and reports progress by writing to the catalog DB. FastAPI stays
responsive; clients poll GET /jobs/{id}.
"""

from __future__ import annotations

import shutil
import threading
import traceback
from pathlib import Path

from . import series_store
from .config import settings
from .db import connect
from .log import get_logger
from .models import JobStatus
from .pipeline import assemble
from .pipeline.imagegen import get_generator

log = get_logger("job")


def _update(book_id: str, **fields) -> None:
    cols = ", ".join(f"{k} = ?" for k in fields)
    vals = list(fields.values()) + [book_id]
    with connect() as conn:
        conn.execute(
            f"UPDATE books SET {cols}, updated_at = datetime('now') WHERE id = ?",
            vals,
        )


# Cooperative pause: a per-book event the image loop polls between scenes.
_pause_events: dict[str, threading.Event] = {}


def request_pause(book_id: str) -> None:
    _pause_events.setdefault(book_id, threading.Event()).set()
    _update(book_id, message="Pausing after current scene…")


def _run(
    book_id: str, epub_path: str, base_prompt: str, series_id: str, images: bool
) -> None:
    book_dir = settings.books_dir / book_id
    out_pack = book_dir / f"{book_id}.bookpack"
    pause_ev = _pause_events.setdefault(book_id, threading.Event())
    pause_ev.clear()  # a fresh run/resume is not paused

    def progress(stage: str, frac: float, msg: str) -> None:
        _update(book_id, stage=stage, progress=frac, message=msg)

    def on_parsed(title: str, author: str) -> None:
        _update(book_id, title=title, author=author)  # show real title during render

    try:
        log.info("Book %s: starting (%s, series=%s)", book_id,
                 "bible-only" if not images else "full", series_id or "-")
        _update(book_id, status=JobStatus.running.value, message="Starting", error=None)
        prior_world, prior_bible = series_store.load_bible(series_id)
        summary = assemble.run_pipeline(
            epub_path=epub_path,
            base_prompt=base_prompt,
            work_dir=book_dir,
            out_pack=out_pack,
            generator=get_generator(),
            progress=progress,
            prior_world=prior_world,
            prior_bible=prior_bible,
            images=images,
            on_parsed=on_parsed,
            pause_check=pause_ev.is_set,
        )
        # Persist what this book contributed back to the series bible.
        series_store.save_bible(series_id, summary.get("world", ""), summary.get("entities", []))
        if summary.get("paused"):
            log.info("Book %s: paused", book_id)
            _update(book_id, status=JobStatus.paused.value, stage="paused",
                    num_scenes=summary["num_scenes"], title=summary["title"],
                    author=summary["author"])
            return
        _update(
            book_id,
            status=JobStatus.done.value,
            stage="done",
            progress=1.0,
            message="Bible extracted" if not summary.get("pack") else "Complete",
            pack_path=str(out_pack) if summary.get("pack") else "",
            num_scenes=summary["num_scenes"],
            title=summary["title"],
            author=summary["author"],
            error=None,
        )
    except Exception as exc:  # noqa: BLE001 - report any failure to the client
        log.exception("Book %s: failed", book_id)
        resumable = (book_dir / "checkpoint.json").exists()
        _update(
            book_id,
            status=JobStatus.error.value,
            message="Failed — Resume to retry from checkpoint" if resumable else "Failed",
            error=f"{exc}\n{traceback.format_exc()}",
        )


def submit_batch(items: list[tuple[str, str, str, str, bool]]) -> None:
    """Process (book_id, epub_path, base_prompt, series_id, images) items in a
    single thread, in order — so within a series each book's bible is available
    to the next one before it starts."""

    def run_all() -> None:
        for book_id, epub_path, base_prompt, series_id, images in items:
            _run(book_id, epub_path, base_prompt, series_id, images)

    threading.Thread(target=run_all, daemon=True).start()


def submit(
    book_id: str,
    epub_path: str | Path,
    base_prompt: str,
    series_id: str = "",
    images: bool = True,
) -> None:
    submit_batch([(book_id, str(epub_path), base_prompt, series_id, images)])


def resume(book_id: str) -> None:
    """Restart a paused book; the pipeline reuses the checkpoint and already-
    generated images, so only the remaining scenes are produced."""
    with connect() as conn:
        row = conn.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
    if not row:
        return
    submit(book_id, row["epub_path"], row["base_prompt"], row["series_id"], images=True)


def reprocess(book_id: str) -> None:
    """Clear cached artifacts (checkpoint + images + pack) and run from scratch,
    so pipeline/prompt changes take effect without re-uploading the EPUB."""
    with connect() as conn:
        row = conn.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
    if not row:
        return
    book_dir = settings.books_dir / book_id
    for name in ("checkpoint.json", "images", "pack"):
        p = book_dir / name
        shutil.rmtree(p, ignore_errors=True) if p.is_dir() else p.unlink(missing_ok=True)
    (book_dir / f"{book_id}.bookpack").unlink(missing_ok=True)
    with connect() as conn:
        conn.execute("UPDATE books SET pack_path='', num_scenes=0 WHERE id=?", (book_id,))
        # If this book is alone in its series, wipe the shared bible so it's
        # rebuilt from scratch — otherwise stale entities/descriptions persist.
        sid = row["series_id"]
        if sid:
            cnt = conn.execute(
                "SELECT COUNT(*) c FROM books WHERE series_id = ?", (sid,)
            ).fetchone()["c"]
            if cnt <= 1:
                conn.execute("DELETE FROM series_entities WHERE series_id = ?", (sid,))
                conn.execute("UPDATE series SET world = '' WHERE id = ?", (sid,))
                log.info("Reprocess %s: cleared series bible (single-book series)", book_id)
    submit(book_id, row["epub_path"], row["base_prompt"], row["series_id"], images=True)
