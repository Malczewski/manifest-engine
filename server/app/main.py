"""FastAPI app: upload UI, book pipeline submission, job status, pack download."""

from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.templating import Jinja2Templates

from . import jobs, series_store, styles
from .config import settings
from .db import connect, init_db
from .log import get_logger
from .models import BookSummary, JobInfo, JobStatus, SeriesInfo

log = get_logger("server")

app = FastAPI(title="Audiobook Visualization Engine", version="0.1.0")
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))


@app.on_event("startup")
def _startup() -> None:
    init_db()
    _log_backends()


def _log_backends() -> None:
    """Print the active backends and whether they're reachable, so a wrong
    config (e.g. falling back to stub/heuristic) is obvious at startup."""
    import httpx

    log.info("segmenter=%s (%s) | image=%s (%s) | compose_prompts=%s",
             settings.segmenter, settings.ollama_model, settings.image_backend,
             settings.image_model, settings.compose_prompts)
    if settings.segmenter == "ollama":
        try:
            httpx.get(f"{settings.ollama_url}/api/tags", trust_env=False, timeout=3).raise_for_status()
            log.info("Ollama: reachable at %s", settings.ollama_url)
        except Exception as exc:  # noqa: BLE001
            log.warning("Ollama unreachable at %s (%s); segmentation will FALL BACK to "
                        "heuristic (fast, no LLM)", settings.ollama_url, exc)
    if settings.image_backend == "drawthings":
        try:
            httpx.get(
                f"{settings.drawthings_url}/sdapi/v1/options", trust_env=False, timeout=3
            ).raise_for_status()
            log.info("Draw Things: reachable at %s", settings.drawthings_url)
        except Exception as exc:  # noqa: BLE001
            log.warning("Draw Things unreachable at %s (%s); image generation will FAIL",
                        settings.drawthings_url, exc)


def _row_to_summary(row) -> BookSummary:
    return BookSummary(
        id=row["id"],
        title=row["title"] or "Untitled",
        author=row["author"] or "Unknown",
        status=JobStatus(row["status"]),
        num_scenes=row["num_scenes"],
        has_pack=bool(row["pack_path"]),
        series_id=row["series_id"],
        series_seq=row["series_seq"],
    )


# --- UI ---------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


# --- API --------------------------------------------------------------------


@app.get("/series", response_model=list[SeriesInfo])
def list_series():
    return [SeriesInfo(**s) for s in series_store.list_series()]


@app.get("/logs", response_class=PlainTextResponse)
def get_logs(n: int = 400):
    """Tail of the engine log (also written to DATA_DIR/engine.log)."""
    path = settings.data_dir / "engine.log"
    if not path.exists():
        return ""
    lines = path.read_text(errors="replace").splitlines()
    return "\n".join(lines[-n:])


@app.get("/styles")
def list_styles():
    return {"default": styles.DEFAULT,
            "styles": [{"key": k, "label": v[0]} for k, v in styles.STYLES.items()]}


@app.post("/books")
async def create_books(
    epubs: list[UploadFile],
    base_prompt: str = Form(""),
    style: str = Form(styles.DEFAULT),
    series_id: str = Form(""),
    series_name: str = Form(""),
    bible_only: bool = Form(False),
):
    """Upload one or more EPUBs. Each new upload forms a series unless series_id
    is given; multiple files are processed in order into the same series so
    earlier books seed the shared bible. bible_only skips image generation."""
    epubs = [e for e in epubs if e.filename and e.filename.lower().endswith(".epub")]
    if not epubs:
        raise HTTPException(400, "Please upload at least one .epub file")

    # Style preset + free-text context become the combined style anchor.
    base_prompt = ", ".join(p for p in (styles.style_text(style), base_prompt.strip()) if p)

    # Resolve the target series: explicit id, else named/new series.
    if not series_id:
        name = series_name or Path(epubs[0].filename).stem
        series_id = series_store.create_series(name)

    batch: list[tuple[str, str, str, str, bool]] = []
    ids: list[str] = []
    for epub in epubs:
        book_id = uuid.uuid4().hex[:12]
        book_dir = settings.books_dir / book_id
        book_dir.mkdir(parents=True, exist_ok=True)
        epub_path = book_dir / "source.epub"
        epub_path.write_bytes(await epub.read())
        seq = series_store.next_seq(series_id) + len(batch)
        with connect() as conn:
            conn.execute(
                "INSERT INTO books(id, base_prompt, epub_path, status, series_id, series_seq) "
                "VALUES (?,?,?,?,?,?)",
                (book_id, base_prompt, str(epub_path), JobStatus.queued.value, series_id, seq),
            )
        batch.append((book_id, str(epub_path), base_prompt, series_id, not bible_only))
        ids.append(book_id)

    jobs.submit_batch(batch)
    return {"ids": ids, "series_id": series_id}


@app.get("/books", response_model=list[BookSummary])
def list_books():
    with connect() as conn:
        rows = conn.execute("SELECT * FROM books ORDER BY created_at DESC").fetchall()
    return [_row_to_summary(r) for r in rows]


@app.get("/jobs/{book_id}", response_model=JobInfo)
def get_job(book_id: str):
    with connect() as conn:
        row = conn.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Unknown book")
    return JobInfo(
        id=row["id"],
        book_id=row["id"],
        status=JobStatus(row["status"]),
        stage=row["stage"],
        progress=row["progress"],
        message=row["message"],
        error=row["error"],
    )


@app.post("/books/{book_id}/pause")
def pause_book(book_id: str):
    jobs.request_pause(book_id)
    return {"paused": book_id}


@app.post("/books/{book_id}/resume")
def resume_book(book_id: str):
    jobs.resume(book_id)
    return {"resumed": book_id}


@app.post("/books/{book_id}/reprocess")
def reprocess_book(book_id: str):
    jobs.reprocess(book_id)
    return {"reprocessing": book_id}


@app.get("/books/{book_id}/pack")
def download_pack(book_id: str):
    with connect() as conn:
        row = conn.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Unknown book")
    if not row["pack_path"] or not Path(row["pack_path"]).exists():
        raise HTTPException(409, "Pack not ready")
    return FileResponse(
        row["pack_path"],
        media_type="application/octet-stream",
        filename=f"{row['title'] or book_id}.bookpack",
    )


@app.delete("/books/{book_id}")
def delete_book(book_id: str):
    import shutil

    with connect() as conn:
        row = conn.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Unknown book")
        conn.execute("DELETE FROM books WHERE id = ?", (book_id,))
    shutil.rmtree(settings.books_dir / book_id, ignore_errors=True)
    return JSONResponse({"deleted": book_id})
