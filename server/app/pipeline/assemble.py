"""Pipeline orchestrator: EPUB path -> finished .bookpack.

Runs the stages in order, reporting progress via a callback so the job runner
can persist it. Image generation dominates wall-clock time, so the pipeline is
built to be interruptible and resumable:

  * The expensive LLM work (sections/segmentation/enrichment/bible) is written to
    a checkpoint.json once; a resume loads it and skips straight to images.
  * Scene images are written to a stable images/ dir and SKIPPED if already
    present, so any interruption (pause, crash, restart) resumes cheaply.
  * A pause_check callback is polled between scenes; when it returns True the run
    stops and reports paused, leaving finished images on disk.
"""

from __future__ import annotations

import dataclasses
import json
import time
from pathlib import Path
from typing import Callable

from ..bookpack import SCHEMA_VERSION, PackWriter
from ..config import settings
from ..log import get_logger
from ..models import Chapter, Entity, Scene
from . import bible, canon, enrich, epub_parse, prompts, sections, statepass, tokenize
from .bible import normalize_name
from .imagegen import ImageGenerator, ImagePromptRejected, StubGenerator
from .segment import segment_book

log = get_logger("pipeline")

# progress(stage, fraction_0_to_1, message)
ProgressFn = Callable[[str, float, str], None]
PauseCheck = Callable[[], bool]


def _noop(stage: str, frac: float, msg: str) -> None:  # pragma: no cover
    pass


def run_pipeline(
    epub_path: str | Path,
    base_prompt: str,
    work_dir: Path,
    out_pack: Path,
    generator: ImageGenerator,
    progress: ProgressFn = _noop,
    prior_world: str = "",
    prior_bible: dict[str, dict] | None = None,
    images: bool = True,
    on_parsed: Callable[[str, str], None] | None = None,
    pause_check: PauseCheck | None = None,
    extra_prompt: str = "",
) -> dict:
    """Execute the full pipeline. Returns a small summary dict.

    prior_world / prior_bible carry a series' shared bible so recurring entities
    reuse their description; the updated world + entities come back in the summary
    for the caller to persist. Set images=False for a bible-only run. work_dir
    holds the checkpoint, the stable images/ dir, and the pack build dir.
    """
    work_dir = Path(work_dir)
    (work_dir / "images").mkdir(parents=True, exist_ok=True)
    checkpoint = work_dir / "checkpoint.json"
    prior_bible = prior_bible or {}

    # --- phase 1: LLM work (or load it from a checkpoint) ---
    if checkpoint.exists():
        progress("resume", 0.28, "Resuming from checkpoint")
        title, author, world, chapters, scenes, entities = _load_checkpoint(checkpoint)
        log.info("Resuming '%s' from checkpoint: %d scenes, %d entities",
                 title, len(scenes), len(entities))
        if on_parsed:
            on_parsed(title, author)
    else:
        result = _prepare(
            epub_path, base_prompt, prior_world, prior_bible, progress, on_parsed, images
        )
        title, author, world, chapters, scenes, entities = result
        if not images:
            progress("done", 1.0, "Bible extracted")
            return {
                "title": title, "author": author, "num_scenes": len(scenes),
                "world": world, "entities": entities, "pack": False,
            }
        _save_checkpoint(checkpoint, title, author, world, chapters, scenes, entities)

    bible_map: dict[str, Entity] = {f"{e.kind}:{e.id}": e for e in entities}

    # --- reference images for bible entities (characters FIRST, then scenes) ---
    # Generate one clean portrait per entity from its stable identity, then feed the
    # relevant references into each scene so a recurring character stays consistent.
    # Auto-on for Gemini (its main consistency lever); opt-in elsewhere.
    if settings.references_enabled:
        refs = [e for e in entities if e.kind == "character"] + \
               [e for e in entities if e.kind != "character"]
        for ent in refs:
            rel = f"images/{ent.kind[:4]}_{ent.id}.png"
            fp = work_dir / rel
            if fp.exists() and fp.stat().st_size:
                ent.image_path = rel
                continue
            if ent.kind == "character":
                ref_prompt = (f"{base_prompt}. Character reference portrait, full figure, "
                              f"neutral pose, plain background. {ent.name}: {ent.descriptor}")
            else:
                ref_prompt = (f"{base_prompt}. Location reference, establishing view, no people. "
                              f"{ent.name}: {ent.descriptor}")
            ref_prompt = _apply_extra(ref_prompt, extra_prompt)
            log.info("Reference image: %s [%s]", ent.name, ent.kind)
            try:
                # Retries transient failures; a reference is a nice-to-have, so a hard
                # failure (e.g. a safety block on a character portrait) is NON-FATAL —
                # log and move on. Scenes still render from the descriptor text, they
                # just won't have this reference to condition on.
                fp.write_bytes(_generate_with_retry(
                    generator, ref_prompt, _seed(ent.id), None, 0, 0, label=f"ref {ent.name}"))
                ent.image_path = rel
            except Exception as exc:  # noqa: BLE001
                log.warning("Reference image FAILED for %s [%s]: %s — continuing without it",
                            ent.name, ent.kind, exc)

    # --- scene images (dominant cost): idempotent + pausable ---
    n = len(scenes)
    cached = sum(1 for s in scenes if (work_dir / f"images/scene_{s.id:04d}.png").exists())
    log.info("Generating scene images: %d scenes (%d already on disk)", n, cached)
    last_by_loc: dict[str, bytes] = {}
    for i, scene in enumerate(scenes):
        rel = f"images/scene_{scene.id:04d}.png"
        scene.image_path = rel
        fp = work_dir / rel
        if fp.exists() and fp.stat().st_size:
            progress("images", 0.30 + 0.60 * ((i + 1) / n), f"Scene {i + 1}/{n} (cached)")
            if settings.continuity_img2img and scene.location_id:
                last_by_loc[scene.location_id] = fp.read_bytes()
            continue
        if pause_check and pause_check():
            log.info("Paused at scene %d/%d", i + 1, n)
            progress("paused", 0.30 + 0.60 * (i / n), f"Paused at scene {i + 1}/{n}")
            return {
                "title": title, "author": author, "num_scenes": n,
                "world": world, "entities": entities, "paused": True,
            }
        # The forward state pass precomputes scene.prompt (identity + temporal
        # overlay); fall back to composing on the fly if it's missing (e.g. a
        # heuristic run or an older checkpoint).
        prompt = scene.prompt or prompts.build_scene_prompt(
            scene, base_prompt, bible_map, world, chapters=chapters)
        # Image-only global style, applied at RENDER time (never baked into the
        # checkpoint) so it can be changed and re-rendered without rerunning the LLM.
        prompt = _apply_extra(prompt, extra_prompt)
        seed = _scene_seed(scene)
        log.info("Scene %d/%d ch%d cast=%s seed=%d\n  prompt: %s",
                 i + 1, n, scene.chapter_idx, scene.characters, seed, prompt)
        init = last_by_loc.get(scene.location_id) if (
            settings.continuity_img2img and scene.location_id) else None
        # Feed this scene's cast (and location) reference images so recurring
        # characters render consistently. Capped to bound input cost/composition.
        ref_images: list[bytes] = []
        if settings.references_enabled:
            for rel_ref in prompts.reference_images(scene, bible_map)[: settings.max_scene_refs]:
                rp = work_dir / rel_ref
                if rp.exists() and rp.stat().st_size:
                    ref_images.append(rp.read_bytes())
        t0 = time.time()
        try:
            png = _generate_with_retry(generator, prompt, seed, init, i + 1, n,
                                       ref_images=ref_images or None)
        except ImagePromptRejected as exc:
            # Content the image model won't draw even after sanitizing/rewording.
            # Don't fail the whole book — write a placeholder and keep going.
            log.warning("Scene %d/%d unrenderable, using placeholder: %s", i + 1, n, exc)
            png = _placeholder_png(f"scene {i + 1}: image unavailable (content filtered)")
        fp.write_bytes(png)
        log.info("Scene %d/%d done in %.1fs (%d KB)", i + 1, n, time.time() - t0, len(png) // 1024)
        if settings.continuity_img2img and scene.location_id:
            last_by_loc[scene.location_id] = png
        progress("images", 0.30 + 0.60 * ((i + 1) / n), f"Scene {i + 1}/{n}")

    # --- assemble the pack (tokens + db + zip) from the stable images dir ---
    _assemble_pack(work_dir, out_pack, title, author, base_prompt, world,
                   chapters, scenes, entities, progress)
    log.info("Complete: '%s' -> %s (%d scenes)", title, out_pack.name, len(scenes))

    progress("done", 1.0, "Complete")
    return {
        "title": title, "author": author, "num_scenes": len(scenes),
        "world": world, "entities": entities, "pack": True,
    }


def _prepare(epub_path, base_prompt, prior_world, prior_bible, progress, on_parsed, images=True):
    """Parse -> sections -> segment -> canon -> bible -> (state pass | harvest)."""
    progress("parse", 0.02, "Parsing EPUB")
    title, author, all_sections = epub_parse.parse_epub(epub_path)
    if not all_sections:
        raise ValueError("No readable sections found in EPUB")
    log.info("Parsed '%s' by %s: %d raw sections", title, author, len(all_sections))
    if on_parsed:
        on_parsed(title, author)

    progress("sections", 0.05, "Selecting story sections")
    chapters = epub_parse.assign_offsets(sections.select_story_sections(all_sections))
    text = epub_parse.whole_text(chapters)
    log.info("Kept %d story section(s), %d chars", len(chapters), len(text))

    progress("segment", 0.10, "Segmenting scenes")
    scenes = segment_book(
        chapters,
        progress=lambda f: progress("segment", 0.10 + 0.12 * f, f"Segmenting scenes {int(f * 100)}%"),
    )
    log.info("Segmented into %d scenes", len(scenes))

    progress("canon", 0.21, "Consolidating character names")
    canon.canonicalize_scenes(scenes)

    progress("bible", 0.22, "Building story bible")
    entities = bible.build_bible(scenes, settings.bible_min_scenes)
    # Carry over reference-image paths from a prior book in the series (idempotent
    # across books; the image files are already in the series work dir). Descriptors
    # are NOT carried over here — enrich() will re-derive them from this book's text
    # using the prior descriptor as seed context (see prior_descs below).
    for e in entities:
        prior = prior_bible.get(normalize_name(e.name))
        if prior and prior.get("image_path"):
            e.image_path = prior["image_path"]
    log.info("Bible: %d entities (%s)", len(entities),
             ", ".join(e.name for e in entities if e.kind == "character")[:200])

    # Cross-book continuity: seed with the series' existing facts + descriptions so
    # recurring entities are extended/refined, not reinvented.
    prior_descs: dict[str, str] = {
        k: v["descriptor"] for k, v in prior_bible.items() if v.get("descriptor")
    }
    prior_facts: dict[str, list[str]] = {
        k: v["facts"] for k, v in prior_bible.items() if v.get("facts")
    }
    n_prior = sum(1 for e in entities if normalize_name(e.name) in prior_bible)
    world = prior_world

    if images:
        # Full run: forward per-scene state pass — accumulates facts, tracks the
        # per-scene overlay, and precomposes every scene.prompt.
        progress("statepass", 0.25, "Analyzing scenes (forward state pass)")
        log.info("State pass over %d scenes, %d entities (%d with prior context, mode=%s)",
                 len(scenes), len(entities), n_prior, settings.state_mode)
        described = statepass.run(
            entities, scenes, chapters, base_prompt,
            prior_world=prior_world, prior_facts=prior_facts, prior_descs=prior_descs,
            progress=lambda f: progress("statepass", 0.10 + 0.15 * f,
                                        f"Analyzing scenes {int(f * 100)}%"),
        )
        world = world or described
    else:
        # Bible-only harvest: cheap chunk-based fact extraction (no per-scene work).
        progress("enrich", 0.25, "Harvesting story bible")
        log.info("Bible harvest over %d entities (%d with prior context)", len(entities), n_prior)
        described = enrich.harvest(
            entities,
            progress=lambda f: progress("enrich", 0.25, f"Describing entities {int(f * 100)}%"),
            scenes=scenes,
            chapters=chapters,
            prior_descs=prior_descs or None,
        )
        world = world or described

    log.info("World: %s", world)
    for e in entities:
        log.info("  %s [%s]: %s", e.name, e.kind, e.descriptor)

    return title, author, world, chapters, scenes, entities


def _assemble_pack(work_dir, out_pack, title, author, base_prompt, world,
                   chapters, scenes, entities, progress):
    progress("index", 0.94, "Writing index")
    text = epub_parse.whole_text(chapters)
    tokens, offsets = tokenize.tokenize(text)
    trigrams = tokenize.build_trigrams(tokens)
    for ch in chapters:
        ch.start_token, ch.end_token = tokenize.token_span_for_offsets(
            offsets, ch.start_offset, ch.end_offset)
    for s in scenes:
        s.start_token, s.end_token = tokenize.token_span_for_offsets(
            offsets, s.start_offset, s.end_offset)

    writer = PackWriter(work_dir / "pack")
    for scene in scenes:
        writer.save_image(scene.image_path, (work_dir / scene.image_path).read_bytes())
    for ent in entities:
        if ent.image_path and (work_dir / ent.image_path).exists():
            writer.save_image(ent.image_path, (work_dir / ent.image_path).read_bytes())

    writer.write_meta({
        "schema_version": SCHEMA_VERSION, "title": title, "author": author,
        "base_prompt": base_prompt, "world": world, "num_scenes": len(scenes),
        "num_chapters": len(chapters), "num_tokens": len(tokens),
    })
    writer.write_chapters(chapters)
    writer.write_scenes(scenes)
    writer.write_tokens(tokens, offsets)
    writer.write_trigrams(trigrams)
    writer.write_entities(entities)
    progress("package", 0.97, "Packaging .bookpack")
    writer.finalize(out_pack)


# --- checkpoint (de)serialization ------------------------------------------


def _save_checkpoint(path: Path, title, author, world, chapters, scenes, entities) -> None:
    path.write_text(json.dumps({
        "title": title, "author": author, "world": world,
        "chapters": [dataclasses.asdict(c) for c in chapters],
        "scenes": [dataclasses.asdict(s) for s in scenes],
        "entities": [dataclasses.asdict(e) for e in entities],
    }))


def _load_checkpoint(path: Path):
    d = json.loads(path.read_text())
    chapters = [Chapter(**c) for c in d["chapters"]]
    scenes = [Scene(**s) for s in d["scenes"]]
    entities = [Entity(**e) for e in d["entities"]]
    return d["title"], d["author"], d["world"], chapters, scenes, entities



def _generate_with_retry(generator, prompt, seed, init, idx, total, attempts=3,
                         ref_images=None, label=""):
    """Generate one image, handling the three failure modes with separate budgets:

      * prompt rejected (content/policy or "couldn't render") -> rewrite the prompt
        (sanitize or reword) up to _MAX_REPHRASES times; if still rejected, re-raise
        the ImagePromptRejected so the caller can decide (scenes -> placeholder).
      * bad request / non-retriable (ImageRequestError) -> re-raise (fatal).
      * transient (network/5xx/empty) -> retry the SAME prompt up to `attempts`.
    """
    from . import compose
    from .imagegen import ImagePromptRejected, ImageRequestError

    who = label or f"scene {idx}/{total}"
    current = prompt
    rewrites = 0
    transient = 0
    while True:
        try:
            return generator.generate(current, seed, init, ref_images)
        except ImagePromptRejected as exc:
            new = None
            if rewrites < _MAX_REPHRASES:
                if getattr(exc, "policy", False):
                    new, kind = compose.sanitize_image_prompt(current), "sanitized"
                else:
                    new, kind = compose.rephrase_image_prompt(current), "rephrased"
            if new and new != current:
                rewrites += 1
                current = new
                log.info("%s prompt rejected; %s (#%d) and retrying:\n  %s",
                         who, kind, rewrites, current)
                continue
            log.warning("%s unrenderable after %d rewrite(s): %s", who, rewrites, exc)
            raise  # let the caller placeholder/skip it
        except ImageRequestError:
            raise  # bad request / config — retrying won't help
        except Exception as exc:  # noqa: BLE001 - transient backend failure
            transient += 1
            log.warning("%s image failed (transient %d/%d): %s", who, transient, attempts, exc)
            if transient < attempts:
                time.sleep(3 * transient)
                continue
            raise RuntimeError(
                f"Image generation failed for {who} after {attempts} attempts "
                f"(is the image backend running?): {exc}"
            ) from exc


# How many times to ask the LLM to rewrite a prompt the image model refused to
# render before giving up on that image.
_MAX_REPHRASES = 2


def _placeholder_png(text: str) -> bytes:
    """A neutral placeholder image for a scene the model refuses to render, so one
    unrenderable scene doesn't fail the whole book (re-attempt later via reset-images)."""
    return StubGenerator().generate(text, 0)


def _apply_extra(prompt: str, extra: str) -> str:
    """Append the book's image-only global style (e.g. 'futuristic sci-fi setting')
    to a render prompt. Kept out of the checkpoint so it's freely editable."""
    extra = (extra or "").strip()
    if not extra:
        return prompt
    return f"{prompt.rstrip('. ')}. {extra}"


def _seed(key: str) -> int:
    import hashlib

    return int(hashlib.sha256(key.encode()).hexdigest()[:8], 16)


def _scene_seed(scene: Scene) -> int:
    """Seed keyed on the (normalized) character cast so a recurring character is
    rendered from the same noise and stays visually consistent across scenes."""
    cast = sorted({normalize_name(c) for c in scene.characters if c.strip()})
    return _seed("|".join(cast) if cast else f"scene:{scene.id}")
