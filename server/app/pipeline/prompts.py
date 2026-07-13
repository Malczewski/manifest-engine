"""Stage 5 — compose the image prompt for a scene and pick reference images.

Prompt = base style + world + location + present characters + action + mood.
Scene names are resolved to canonical bible entities via normalize_name (the same
merge logic bible.py uses), so "Reyes" in a scene still finds the "Captain Reyes"
entity's description and reference image.
"""

from __future__ import annotations

from ..models import Chapter, Entity, Scene
from . import compose
from .bible import normalize_name


def _index(bible: dict[str, Entity], kind: str) -> dict[str, Entity]:
    return {normalize_name(e.name): e for e in bible.values() if e.kind == kind}


def _resolve(name: str, index: dict[str, Entity]) -> Entity | None:
    norm = normalize_name(name)
    if norm in index:
        return index[norm]
    for key, ent in index.items():
        if norm and (norm in key or key in norm):
            return ent
    return None


def _scene_body(scene: Scene, chapters: list[Chapter]) -> str:
    """Extract the raw text for this scene from its chapter."""
    for ch in chapters:
        if ch.idx == scene.chapter_idx:
            lo = max(0, scene.start_offset - ch.start_offset)
            hi = min(len(ch.text), scene.end_offset - ch.start_offset)
            return ch.text[lo:hi].strip()
    return ""


def build_scene_prompt(
    scene: Scene,
    base_prompt: str,
    bible: dict[str, Entity],
    world: str = "",
    chapters: list[Chapter] | None = None,
) -> str:
    """Compose the prompt from style + world + entity *descriptions* + action.

    Using the descriptions (not just names) is what keeps the image semantically
    correct — otherwise the model invents its own idea of who "Cara" is.

    When chapters is provided the raw scene text is reconstructed and passed to
    the compose LLM so it can pick up scene-specific clothing / visible state.
    """
    chars = _index(bible, "character")
    locs = _index(bible, "location")

    loc = _resolve(scene.location_id.replace("_", " "), locs) if scene.location_id else None
    present: list[Entity] = []
    seen: set[str] = set()
    for c in scene.characters:
        ent = _resolve(c, chars)
        if ent and ent.descriptor and ent.id not in seen:
            seen.add(ent.id)
            present.append(ent)

    action = scene.key_action or scene.summary
    body = _scene_body(scene, chapters) if chapters else ""

    scene_line = compose.compose_scene_line(
        style=base_prompt,
        world=world,
        location=(loc.descriptor or loc.name) if loc else "",
        names=scene.characters,
        action=action,
        mood=scene.mood,
        time_of_day=scene.time_of_day,
        scene_body=body,
    )
    if not scene_line:
        bits = [action]
        if loc:
            bits.append("at " + loc.name)
        if scene.time_of_day:
            bits.append(scene.time_of_day)
        scene_line = ", ".join(b for b in bits if b)

    parts: list[str] = []
    if base_prompt.strip():
        parts.append(base_prompt.strip())
    parts.append(scene_line)
    for ent in present:
        if ent.descriptor:
            parts.append(f"{ent.name}: {ent.descriptor}")
    parts.append("highly detailed, coherent anatomy")
    return " ".join(p.rstrip(". ") + "." for p in parts if p.strip())


def reference_images(scene: Scene, bible: dict[str, Entity]) -> list[str]:
    """Relative pack paths of reference images to condition this scene on."""
    chars = _index(bible, "character")
    locs = _index(bible, "location")
    refs: list[str] = []
    loc = _resolve(scene.location_id.replace("_", " "), locs) if scene.location_id else None
    if loc and loc.image_path:
        refs.append(loc.image_path)
    for c in scene.characters:
        ent = _resolve(c, chars)
        if ent and ent.image_path:
            refs.append(ent.image_path)
    return refs
