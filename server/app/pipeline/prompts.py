"""Stage 5 — assemble the image prompt for a scene and pick reference images.

Prompt = base style + scene line (action/location, in-scene only) + per-entity
identity (STABLE descriptor, appended verbatim so the look + per-cast seed stay
constant) + per-entity temporal OVERLAY (current outfit/condition at this scene).

Scene names are resolved to canonical bible entities via normalize_name (the same
merge logic bible.py uses), so "Reyes" in a scene still finds the "Captain Reyes"
entity's description and reference image.

The forward state pass (statepass.py) precomposes the scene line and fills
scene.overlays, then calls assemble_scene_prompt. build_scene_prompt is the
fallback for paths without a state pass (heuristic / no-LLM): it composes the
line itself, then assembles the same way.
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
    for ch in chapters:
        if ch.idx == scene.chapter_idx:
            lo = max(0, scene.start_offset - ch.start_offset)
            hi = min(len(ch.text), scene.end_offset - ch.start_offset)
            return ch.text[lo:hi].strip()
    return ""


def _present_characters(scene: Scene, chars: dict[str, Entity]) -> list[Entity]:
    present: list[Entity] = []
    seen: set[str] = set()
    for c in scene.characters:
        ent = _resolve(c, chars)
        if ent and ent.descriptor and ent.id not in seen:
            seen.add(ent.id)
            present.append(ent)
    return present


def assemble_scene_prompt(
    scene: Scene, base_prompt: str, bible: dict[str, Entity], scene_line: str
) -> str:
    """Build the final prompt from a precomposed scene line + bible identities +
    this scene's temporal overlays (scene.overlays)."""
    chars = _index(bible, "character")
    locs = _index(bible, "location")
    loc = _resolve(scene.location_id.replace("_", " "), locs) if scene.location_id else None
    present = _present_characters(scene, chars)

    if not scene_line:
        bits = [scene.key_action or scene.summary]
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
        line = f"{ent.name}: {ent.descriptor}"
        overlay = scene.overlays.get(ent.name)
        if overlay:
            line += f", currently {overlay}"
        parts.append(line)
    parts.append("highly detailed, coherent anatomy")
    return " ".join(p.rstrip(". ") + "." for p in parts if p.strip())


def build_scene_prompt(
    scene: Scene,
    base_prompt: str,
    bible: dict[str, Entity],
    world: str = "",
    chapters: list[Chapter] | None = None,
) -> str:
    """Fallback prompt builder (no state pass): compose the scene line here, then
    assemble it with identities + overlays exactly like the state pass would."""
    locs = _index(bible, "location")
    loc = _resolve(scene.location_id.replace("_", " "), locs) if scene.location_id else None
    body = _scene_body(scene, chapters) if chapters else ""

    scene_line = compose.compose_scene_line(
        style=base_prompt,
        world=world,
        location=(loc.descriptor or loc.name) if loc else "",
        names=scene.characters,
        action=scene.key_action or scene.summary,
        mood=scene.mood,
        time_of_day=scene.time_of_day,
        scene_body=body,
    ) or ""
    return assemble_scene_prompt(scene, base_prompt, bible, scene_line)


def reference_images(scene: Scene, bible: dict[str, Entity]) -> list[str]:
    """Relative pack paths of reference images to condition this scene on.

    Characters come FIRST (they're the consistency priority, so a per-scene cap
    keeps them over the location), the location reference last.
    """
    chars = _index(bible, "character")
    locs = _index(bible, "location")
    refs: list[str] = []
    seen: set[str] = set()
    for c in scene.characters:
        ent = _resolve(c, chars)
        if ent and ent.image_path and ent.image_path not in seen:
            seen.add(ent.image_path)
            refs.append(ent.image_path)
    loc = _resolve(scene.location_id.replace("_", " "), locs) if scene.location_id else None
    if loc and loc.image_path and loc.image_path not in seen:
        refs.append(loc.image_path)
    return refs
