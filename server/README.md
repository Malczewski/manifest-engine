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
and `SEGMENTER=heuristic` to run fully offline (placeholder images, no LLM). Set
`SEGMENTER=gemini` with a `GEMINI_API_KEY` to use Google Gemini instead of a local
Ollama server. The startup banner logs the active backends and whether they're reachable.

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
| `SEGMENTER` | `heuristic` | `heuristic` \| `ollama` \| `gemini` |
| `OLLAMA_URL` | `http://127.0.0.1:11434` | Ollama server |
| `OLLAMA_MODEL` | `gemma4:12b` | segmentation + enrichment model (Ollama backend) |
| `GEMINI_API_KEY` | (blank) | Google AI API key (`SEGMENTER=gemini`) |
| `GEMINI_MODEL` | `gemini-2.5-flash` | Gemini model name (see Model choice below) |
| `TARGET_SCENE_CHARS` | `1800` | approx scene length |
| `STATE_MODE` | (auto) | forward state pass: `fuse` (1 call/scene) \| `per_scene` (2 calls/scene). Auto = `fuse` on Gemini, `per_scene` on Ollama |
| `ENRICH_CHUNK_CHARS` | `24000` | chunk size for the bible-only harvest (≤ the LLM's context) |
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

**Forward state pass (whole-book, temporal):** the pipeline walks the book's
scenes **in order** ([pipeline/statepass.py](app/pipeline/statepass.py)),
maintaining an evolving per-entity state:

- **facts** — stable physical traits (species, hair, eyes, scars) that
  **accumulate** as the book reveals them. These never include clothing or mood.
- **overlay** — the entity's current scene-specific visible state (outfit, injury,
  dirt) that **overrides** and then **carries forward** until the text changes it.

Each scene's image prompt = base style + a composed action line + each present
entity's **stable identity** (synthesized from the accumulated facts, appended
verbatim so the look and the per-cast seed stay constant) + that entity's
**overlay as of this scene**. This is temporally correct: a scar acquired in
chapter 20 does not appear in chapter 1, and a cloak put on in scene 5 persists
through later scenes without being restated. The per-scene analysis runs *before*
image generation and is checkpointed, so a resume never re-runs LLM work.

`STATE_MODE` picks how the per-scene work runs: **`fuse`** does the analysis and
the action line in one LLM call per scene (fewest calls — default on Gemini);
**`per_scene`** splits them into two simpler calls (default on Ollama, better for
weaker local models). Bible-only runs skip this and use a cheaper chunked fact
**harvest** ([pipeline/enrich.py](app/pipeline/enrich.py)) instead.

In a **series**, an earlier book's accumulated facts + descriptor seed the next
book's state, so a recurring character is extended/refined; a later book overrides
the look only where its own text contradicts the earlier one. Facts and
descriptors persist per series in `engine.db` (`series_entities`).

**Consistency (Approach A, default):** rich per-entity descriptions from the
enrichment stage + a seed derived from each scene's character cast, so a recurring
character renders consistently. Reference-image / Kontext conditioning is opt-in
(`GENERATE_REFERENCES`, `CONTINUITY_IMG2IMG`).

Example with local Ollama:

```bash
IMAGE_BACKEND=drawthings SEGMENTER=ollama OLLAMA_MODEL=qwen2.5:14b \
  uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Example with Gemini (no local GPU required for the LLM stage):

```bash
IMAGE_BACKEND=drawthings SEGMENTER=gemini GEMINI_API_KEY=your_key \
  GEMINI_MODEL=gemini-2.5-flash uvicorn app.main:app --host 0.0.0.0 --port 8000
```

**Model choice (free tier).** The pipeline is sequential (one call at a time), so
RPM/TPM are never the bottleneck — the binding limits are **requests/day (RPD)** and
model quality. A ~100k-word novel in `fuse` mode is ~375 calls (≈70 segmentation
windows + 1 canon + ~300 scene calls + 1 consolidate); `per_scene` roughly doubles
the scene calls.

| Model | RPD (free) | Notes |
|---|---|---|
| `gemini-2.5-flash` (default) | 10k | Capable enough for the fused state pass; ~25 books/day. Safe, proven id. |
| `gemini-3.5-flash` | 10k | Newest/most capable Flash — best quality; switch to it once the token test below confirms the id. |
| `gemini-2.0-flash` / `gemini-2.5-flash-lite` | unlimited | Highest daily volume; lite is weaker at the fused reasoning. |
| `gemini-3.1-pro` | 250 | **Too low** — a single book exceeds it. |

Each call is small (~2–6k tokens vs a 1M-token context, and 1–4M TPM), so context
size is never a concern. The Gemini path auto-retries 429s with backoff, so hitting
a per-minute cap only slows processing, never fails it.

**Test your API key (single request)** — before running the whole pipeline:

```bash
# 1) Does the token work + is the model id valid?
curl -s "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key=$GEMINI_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"contents":[{"parts":[{"text":"Reply with just: OK"}]}]}'

# 2) List the exact model ids your key can use (to confirm 3.5-flash etc.)
curl -s "https://generativelanguage.googleapis.com/v1beta/models?key=$GEMINI_API_KEY" \
  | grep '"name"'
```

Or exercise our integration end to end (JSON-mode, same code path the pipeline uses):

```bash
GEMINI_API_KEY=your_key SEGMENTER=gemini GEMINI_MODEL=gemini-2.5-flash \
  python3 -c 'from app.pipeline import llm; print(llm.call_json("Return JSON {\"ok\": true}"))'
```

A `{'ok': True}` (or an `OK` from the curl) confirms the key, the model id, and the
JSON-schema plumbing all work.

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

## LLM logic — lessons & gotchas

Hard-won findings from tuning segmentation / bible / enrichment / prompts. **Read
before changing the LLM pipeline** — most of these were re-learned the hard way.

1. **Describe entities in ONE batch call, not individually.** Describing all
   entities together (see `enrich._PROMPT`) keeps them *distinct* — the model gives
   dog features to the dogs and human features to the girl. Describing a character
   *in isolation* from text full of other beings makes it conflate them (Cara came
   out "half-canine hybrid"). Grounding a description in only that character's scenes
   backfires the same way when they share scenes with creatures.
2. **Prompts must use DESCRIPTIONS, not just names.** "characters: Cara" → the image
   model invents a generic girl / real dog / wooden house. The whole point of the
   bible is concrete per-entity descriptions.
3. **Don't dump the world into every scene.** Prepending the full world paragraph
   made every image include every world prop (alien dogs, "stick moons"). Compose
   each scene prompt with the LLM (`compose.py`) using only what's in that scene;
   pass the world as *context only, do not dump*.
4. **Attribute body parts/actions to explicit subjects.** "ears twitching, soft
   paws" (the dogs') leaked onto Cara → elf/animal ears. A negative prompt can't
   override an explicit positive mention — the composer must never give humans
   animal features and must tie each feature to a named subject.
5. **"Same as X" cross-references** appear when near-duplicate names are described
   together. The "don't say same as" instruction is ignored by small models. Defense
   in depth: filter junk entities first, deterministically resolve/clean cross-refs,
   and re-describe leftovers individually.
6. **Coreference is hard for small models — resolve it at segmentation, in scene
   context.** Tell the segmenter to prefer proper names and expand groups ("her
   parents" → the individuals). Be conservative with any *global* merge (never merge
   different people's "father"); drop what can't be resolved rather than guess.
7. **Filter the bible to what needs cross-scene consistency.** Keep recurring
   (≥ `BIBLE_MIN_SCENES` scenes) AND specific entities (proper name, "X's mother", or
   a creature). Drop pronoun-relations ("her parents"), generic roles/collectives
   ("the soldiers"), and one-offs — the composer handles those per scene.
8. **Force valid JSON with a schema, not `format:"json"`.** Some models (gemma) emit
   malformed JSON, which silently fell back to heuristic segmentation → empty bible.
   Use Ollama `format=<schema>` (see `segment._SCENES_SCHEMA`, `enrich._ENRICH_SCHEMA`),
   a list (not dynamic-key dict) for entity output, and tolerant parsing (`jsonutil`).
9. **Demand concrete, committal descriptions; ban vague filler.** Models differ:
   qwen commits to specifics (and sometimes hallucinates); gemma is faithful but
   vague ("practical clothing" → nothing for the illustrator to anchor, so the look
   drifts). Pin the specificity in the prompt, not the model.
10. **Mind the caches when iterating.** LLM output is cached in `checkpoint.json`
    (per book) and in `series_entities` (per series). A prompt/logic fix won't show
    until the relevant cache is cleared: **Reprocess** clears both (single-book
    series); **Resume** deliberately reuses them.
11. **Make fallbacks loud.** Silent heuristic/stub fallback looks like success. Log
    warnings on fallback and check backend reachability at startup (see the
    `[engine]` banner).
12. **Thinking models:** set `"think": false` for clean structured output (qwen3),
    and never route localhost LLM/image calls through a proxy (`trust_env=False`).
13. **Separate permanent identity from temporal state — and be strict about which
    is which.** The identity descriptor is built from ALL the book's facts and
    appended to EVERY scene, so anything misfiled as a "fact" leaks backward: an
    *acquired* scar (chapter-20 fight) recorded as a permanent mark reappears in
    chapter 1. The rule: **facts = INHERENT/born-with only** (species, hair, eyes,
    build, freckles, birthmarks); **anything acquired during the story** (wounds,
    event-scars, blood, ageing, haircuts, outfits) → the per-scene **overlay**,
    which is forward-only and carries forward until it changes. Enforce it in the
    extraction prompt AND with a deterministic backstop (`enrich.strip_transient_facts`).
14. **Bound the running state or it fills the context.** Small models re-emit known
    facts every scene despite "don't repeat"; appending blindly grows the per-entity
    state, and since it's re-sent in each scene's prompt, every call gets bigger
    (memory + token cost balloon). De-dupe facts ON INSERT and cap them
    (`_MAX_FACTS`), cap the overlay length, and cap the world-hint list.

## `.bookpack` format

A zip containing `book.db` (SQLite) + `images/`. The app reads `book.db`:
`meta`, `chapters`, `scenes` (with `[start_token,end_token)`, `image_path`, and the
composed `prompt`), `tokens` (normalized stream with char offsets), `trigrams`
(inverted index for fuzzy matching), and `entities` (character/location bible with
`descriptor` + accumulated `facts`). Schema is versioned (`SCHEMA_VERSION`); full
column list is documented in [app/bookpack.py](app/bookpack.py).

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
    segment.py       scenes (heuristic | ollama | gemini, windowed)
    canon.py         consolidate entity name variants across the book (LLM)
    bible.py         characters/locations (+ name normalization/dedup)
    statepass.py     forward per-scene state pass: facts (accumulate) + overlay (temporal)
    enrich.py        bible-only fact harvest (chunked) + fact->descriptor consolidation
    compose.py       LLM-composed per-scene action line (no appearance/clothing)
    llm.py           LLM backend dispatcher (Ollama <-> Gemini)
    prompts.py       scene prompt + entity resolution
    imagegen.py      ImageGenerator: stub | drawthings
    tokenize.py      normalized tokens + trigram index
    assemble.py      orchestrator: checkpoint + resumable/pausable images -> pack
templates/index.html upload UI (multi-file, series, bible-only)
```
