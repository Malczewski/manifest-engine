# Audiobook Visualization Engine — Server

Pre-processing service: turns an EPUB into a `.bookpack` (scenes + consistent
images + a token index) that the Android app downloads and plays along with an
audiobook. See the architecture plan for the full design.

## Run

```bash
cd server
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt        # regular PyPI registry

uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Open http://localhost:8000 — upload an EPUB + optional base style prompt, watch
the pipeline run, and download the resulting `.bookpack`.

The real backends (Draw Things + Ollama) are the defaults; set `IMAGE_BACKEND=stub`
and `SEGMENTER=heuristic` to run fully offline (placeholder images, no LLM). The
startup banner logs the active backends and whether they're reachable.

## Logs

Progress is logged to the console and to `DATA_DIR/engine.log` — stages, the
chosen scene prompt + seed + timing per image, warnings, and failures. Tail it
with `tail -f data/engine.log`, open the **logs ↗** link in the UI, or hit
`GET /logs?n=400`.

## Configuration (env vars)

| Var | Default | Meaning |
|---|---|---|
| `DATA_DIR` | `server/data` | where books, packs, and the catalog DB live |
| `IMAGE_BACKEND` | `stub` | `stub` \| `drawthings` |
| `DRAWTHINGS_URL` | `http://127.0.0.1:7860` | Draw Things API server (enable *API Server* in the app) |
| `IMAGE_WIDTH` / `IMAGE_HEIGHT` | `768` / `512` | generated image size |
| `IMAGE_STEPS` | `20` | sampler steps |
| `IMAGE_MODEL` | (blank) | Draw Things model/checkpoint name; blank = app default |
| `SEGMENTER` | `heuristic` | `heuristic` \| `ollama` |
| `OLLAMA_URL` | `http://127.0.0.1:11434` | Ollama server |
| `OLLAMA_MODEL` | `qwen3:8b` | segmentation + enrichment model |
| `TARGET_SCENE_CHARS` | `1800` | approx scene length |
| `COMPOSE_PROMPTS` | `1` | LLM-compose each scene prompt (only in-scene detail); `0` = mechanical |
| `NEGATIVE_PROMPT` | (quality defaults) | negative prompt for every image |
| `GENERATE_REFERENCES` | `0` | `1` to also generate per-entity reference images |
| `CONTINUITY_IMG2IMG` | `0` | `1` to img2img consecutive same-location scenes |
| `IMG2IMG_DENOISE` | `0.65` | denoise strength when continuity is on |

**Visual style:** the upload form has a **style selector** (default *Digital
painting* — illustrated, not photo, not flat cartoon; also Painterly, Graphic
novel, Watercolor, Storybook, Cinematic, Photorealistic, Anime). The preset text
plus your free-text "extra context" form the style anchor on every scene. Presets
live in [app/styles.py](app/styles.py).

**Consistency (Approach A, default):** rich per-entity descriptions from the
enrichment stage + a seed derived from each scene's character cast, so a recurring
character renders consistently. Reference-image / Kontext conditioning is opt-in
(`GENERATE_REFERENCES`, `CONTINUITY_IMG2IMG`).

Example with real backends:

```bash
IMAGE_BACKEND=drawthings SEGMENTER=ollama OLLAMA_MODEL=qwen2.5:14b \
  uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## HTTP API

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/` | upload UI |
| `POST` | `/books` | multipart: `epubs` file(s) + `style`, `base_prompt`, `series_id`/`series_name`, `bible_only` -> `{ids, series_id}` |
| `GET` | `/books` | list books + status (incl. `series_id`, `series_seq`) |
| `GET` | `/series` | list series + book counts |
| `GET` | `/styles` | visual-style presets for the selector |
| `GET` | `/jobs/{id}` | pipeline progress for a book |
| `POST` | `/books/{id}/pause` | pause after the current scene |
| `POST` | `/books/{id}/resume` | resume from checkpoint + existing images |
| `GET` | `/books/{id}/pack` | download the `.bookpack` |
| `DELETE` | `/books/{id}` | remove a book and its files |

## Pausing / resuming (long renders)

Image generation dominates wall-clock time, so a run is **interruptible**:

- After the LLM work (segmentation/enrichment/bible), the pipeline writes a
  `checkpoint.json` in the book's work dir — a resume skips straight to images.
- Scene images go to a stable `images/` dir and are **skipped if already present**,
  so **any** interruption (pause, crash, server restart) resumes cheaply.
- `POST /books/{id}/pause` stops after the current scene (status → `paused`);
  `POST /books/{id}/resume` (or the UI button) generates only the remaining scenes,
  then assembles the pack. Re-uploading isn't needed — the checkpoint drives it.

Prompt quality: each scene prompt is composed by the LLM
([pipeline/compose.py](app/pipeline/compose.py)) to mention only what's in that
scene, while canonical character descriptions are appended verbatim so identity
(and the per-cast seed) stays consistent.

## Series (shared bible)

Books in a **series** share a character/location bible. Each new upload forms a
series unless you pick an existing one; multiple files upload into one series and
process **in order**, so earlier books seed later ones. Recurring characters keep
the same description across books — which, under Approach A, also yields the same
seed and therefore a consistent look.

**Bible-only** (`bible_only`) runs segmentation + enrichment and stores the bible
for the series, but skips image generation and produces no pack. Use it to harvest
rich descriptions from earlier books cheaply, then generate images for the book you
actually want to visualize. Shared bible tables live in `engine.db`
(`series`, `series_entities`) — see [app/series_store.py](app/series_store.py).

## `.bookpack` format

A zip containing `book.db` (SQLite) + `images/`. The app reads `book.db`:
`meta`, `chapters`, `scenes` (with `[start_token,end_token)` + `image_path`),
`tokens` (normalized stream with char offsets), `trigrams` (inverted index for
fuzzy matching), and `entities` (character/location bible). Full column list is
documented in [app/bookpack.py](app/bookpack.py).

## Layout

```
app/
  main.py            FastAPI app: UI + API
  config.py          env-driven settings
  db.py              catalog/job/series SQLite
  jobs.py            background pipeline worker (sequential per batch)
  series_store.py    series catalog + shared-bible persistence
  bookpack.py        .bookpack writer (the app-facing contract)
  models.py          shared domain + API types
  pipeline/
    epub_parse.py    EPUB -> sections + offsets
    sections.py      keep story sections, drop front/back matter + previews
    segment.py       scenes (heuristic | ollama, windowed)
    bible.py         characters/locations (+ name normalization/dedup)
    enrich.py        world context + per-entity visual descriptions (LLM)
    compose.py       LLM-composed per-scene prompt line (only in-scene detail)
    prompts.py       scene prompt + entity resolution
    imagegen.py      ImageGenerator: stub | drawthings
    tokenize.py      normalized tokens + trigram index
    assemble.py      orchestrator: checkpoint + resumable/pausable images -> pack
templates/index.html upload UI (multi-file, series, bible-only)
```
