"""Stage 4 — forward per-scene STATE PASS (full runs).

This is the heart of the temporal model. We walk the book's scenes IN ORDER and
maintain, per entity, an evolving state:

    state[entity] = {
        facts:   [stable physical facts accumulated so far],   # permanent
        overlay: "current visible outfit / condition",         # scene-specific
    }

For each scene we ask the LLM (see the two modes below) to (a) write the scene's
action line and (b) report, for each present entity, any newly revealed STABLE
facts and the entity's CURRENT scene-specific overlay. Facts ACCUMULATE; the
overlay OVERRIDES and then carries forward until the text changes it again.

Why this shape:
  * Temporal correctness — scene N is rendered with the overlay in effect AT
    scene N, so a scar acquired in chapter 20 doesn't appear in chapter 1.
  * Identity stability — the permanent identity (built from the accumulated facts
    via enrich.consolidate) is appended VERBATIM to every scene, so the character's
    core look and the per-cast seed stay constant. Only the overlay changes.

Modes (settings.state_mode):
  * "fuse"      — ONE LLM call per scene returns the scene line + state deltas.
                  Fewest calls; best on capable hosted models.
  * "per_scene" — TWO calls per scene (extract state, then compose the line).
                  Each call is simpler; better for weaker local models.

The per-scene LLM work happens here, BEFORE image generation, and the resulting
scene.prompt / scene.overlays / entity.facts are checkpointed — so a resume never
re-runs any LLM work.
"""

from __future__ import annotations

import time

from . import compose, enrich, llm, prompts
from .bible import normalize_name
from ..config import settings
from ..log import get_logger
from ..models import Chapter, Entity, Scene

log = get_logger("statepass")

# ---------------------------------------------------------------------------
# Prompts + schemas
# ---------------------------------------------------------------------------

_STATE_RULES = """For each listed entity present in THIS scene, report two things.
Draw a HARD line between INHERENT traits and ACQUIRED / current state:

- "new_facts": ONLY INHERENT, born-with traits NEWLY revealed here — species,
  approximate age, build/height, hair colour & style, eye colour, skin tone,
  freckles, birthmarks. These are TIMELESS (true in EVERY scene of the book), so
  they must never describe something that only became true partway through the
  story. Return [] if nothing new. Do NOT repeat facts already in the known state.
  NEVER put here: clothing, mood, actions, plot, or anything ACQUIRED during the
  story — wounds, cuts, a scar from a fight, blood, bruises, dirt, a new haircut,
  ageing, a lost limb. Those are NOT inherent → they go in "overlay".

- "overlay": the entity's CURRENT visible state AS OF THIS SCENE — what they are
  WEARING now, plus any condition ACQUIRED by this point (injury, a scar from an
  earlier event, blood, dirt, disguise, soaked, visibly older). This carries
  forward to later scenes until it changes, so lasting acquired marks belong HERE,
  not in facts. Keep it to one short phrase. Return "" ONLY when the scene gives no
  clothing/appearance cue (the previous overlay is then kept).

Never give a human character animal features. Keep entities distinct."""

_FUSE_PROMPT = """You are illustrating a book, scene by scene. Do BOTH tasks for THIS scene.

1) "scene_line": ONE vivid sentence (<=40 words) describing ONLY what is visible in
   THIS scene — the listed characters performing the action, in the location. Do NOT
   describe any character's permanent appearance or their clothing (handled separately).
   Attribute every action/body part to an explicit named subject.

2) """ + _STATE_RULES + """

Return JSON: {{"scene_line": "...", "world_hint": "...",
  "entities": [{{"name": "...", "new_facts": ["..."], "overlay": "..."}}]}}.

Known state so far (extend, do not repeat):
{state_block}

Art style: {style}
World (context only, do not dump): {world}
Location: {location}
Characters present: {names}
Action: {action}
Mood: {mood}. Time of day: {tod}.

Scene text:
{scene_text}
"""

_EXTRACT_PROMPT = """Track character/entity STATE for illustrating a book, scene by scene.
""" + _STATE_RULES + """

Return JSON: {{"world_hint": "...",
  "entities": [{{"name": "...", "new_facts": ["..."], "overlay": "..."}}]}}.

Known state so far (extend, do not repeat):
{state_block}

Entities present: {names}

Scene text:
{scene_text}
"""

_ENTITY_ITEM = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "new_facts": {"type": "array", "items": {"type": "string"}},
        "overlay": {"type": "string"},
    },
    "required": ["name"],
}

_FUSE_SCHEMA = {
    "type": "object",
    "properties": {
        "scene_line": {"type": "string"},
        "world_hint": {"type": "string"},
        "entities": {"type": "array", "items": _ENTITY_ITEM},
    },
    "required": ["scene_line", "entities"],
}

_EXTRACT_SCHEMA = {
    "type": "object",
    "properties": {
        "world_hint": {"type": "string"},
        "entities": {"type": "array", "items": _ENTITY_ITEM},
    },
    "required": ["entities"],
}


# ---------------------------------------------------------------------------
# State container
# ---------------------------------------------------------------------------

class _EntityState:
    __slots__ = ("facts", "overlay")

    def __init__(self, facts: list[str] | None = None, overlay: str = ""):
        self.facts: list[str] = list(facts or [])
        self.overlay: str = overlay


def _state_block(present: list[tuple[Entity, _EntityState]]) -> str:
    lines = []
    for ent, st in present:
        facts = "; ".join(st.facts[:10]) or "(none yet)"
        wearing = st.overlay or "(unknown)"
        lines.append(f"- {ent.name}: facts=[{facts}]; currently={wearing}")
    return "\n".join(lines) or "(no tracked entities)"


# ---------------------------------------------------------------------------
# Per-scene LLM calls
# ---------------------------------------------------------------------------

# Bounds so a chatty model can't grow the running state (and thus every
# subsequent prompt) without limit. Inherent traits are few; more than this is
# duplication or noise, and an overlay is meant to be one short phrase.
_MAX_FACTS = 12
_MAX_OVERLAY_CHARS = 200


def _apply_deltas(
    data: dict,
    present_by_norm: dict[str, tuple[Entity, _EntityState]],
) -> None:
    """Fold one scene's reported deltas into the running states (in place).

    Facts are de-duplicated ON INSERT (small models re-emit known facts despite
    the prompt) and capped, so the state we re-send each scene stays bounded."""
    for item in data.get("entities", []):
        if not isinstance(item, dict) or not item.get("name"):
            continue
        norm = normalize_name(str(item["name"]))
        match = present_by_norm.get(norm)
        if not match:
            # tolerate the model naming an entity slightly differently
            match = next(
                (v for k, v in present_by_norm.items() if norm and (norm in k or k in norm)),
                None,
            )
        if not match:
            continue
        _, st = match
        have = {f.lower() for f in st.facts}
        for f in item.get("new_facts", []):
            f = str(f).strip()
            if f and f.lower() not in have and len(st.facts) < _MAX_FACTS:
                st.facts.append(f)
                have.add(f.lower())
        overlay = str(item.get("overlay", "")).strip()
        if overlay:
            st.overlay = overlay[:_MAX_OVERLAY_CHARS]  # override + carry forward


def _process_scene(
    scene: Scene,
    scene_text: str,
    present: list[tuple[Entity, _EntityState]],
    base_prompt: str,
    world: str,
    location_desc: str,
) -> tuple[str, str]:
    """Run the per-scene LLM work; returns (scene_line, world_hint). Updates the
    present entities' states in place."""
    present_by_norm = {normalize_name(e.name): (e, st) for e, st in present}
    names = ", ".join(e.name for e, _ in present) or "none"
    action = scene.key_action or scene.summary

    if settings.state_mode == "fuse":
        prompt = _FUSE_PROMPT.format(
            state_block=_state_block(present),
            style=base_prompt or "cinematic illustration",
            world=world[:400],
            location=location_desc or "unspecified",
            names=names,
            action=action or "",
            mood=scene.mood or "",
            tod=scene.time_of_day or "",
            scene_text=scene_text[:1600],
        )
        try:
            data = llm.call_json(prompt, schema=_FUSE_SCHEMA, temperature=0.4)
        except Exception as exc:
            log.warning("fuse LLM call failed: %s", exc)
            return "", ""
        _apply_deltas(data, present_by_norm)
        return str(data.get("scene_line", "")).strip(), str(data.get("world_hint", "")).strip()

    # per_scene: extract state, then compose the line separately
    hint = ""
    try:
        data = llm.call_json(
            _EXTRACT_PROMPT.format(
                state_block=_state_block(present),
                names=names,
                scene_text=scene_text[:1600],
            ),
            schema=_EXTRACT_SCHEMA,
            temperature=0.2,
        )
        _apply_deltas(data, present_by_norm)
        hint = str(data.get("world_hint", "")).strip()
    except Exception as exc:
        log.warning("extract LLM call failed: %s", exc)
    line = compose.compose_scene_line(
        style=base_prompt,
        world=world,
        location=location_desc,
        names=scene.characters,
        action=action,
        mood=scene.mood,
        time_of_day=scene.time_of_day,
        scene_body=scene_text,
    )
    return (line or ""), hint


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run(
    entities: list[Entity],
    scenes: list[Scene],
    chapters: list[Chapter],
    base_prompt: str,
    *,
    prior_world: str = "",
    prior_facts: dict[str, list[str]] | None = None,
    prior_descs: dict[str, str] | None = None,
    progress=None,
) -> str:
    """Forward state pass over all scenes. Fills, in place:
      * entity.facts + entity.descriptor (via enrich.consolidate)
      * scene.overlays (per-entity state at that scene) + scene.prompt

    Returns the world string. prior_* carry a series' shared state so recurring
    entities start seeded and are extended rather than reinvented.
    """
    prior_facts = prior_facts or {}
    prior_descs = prior_descs or {}
    if not settings.use_llm:
        return prior_world

    ch_map = {c.idx: c for c in chapters}
    char_by_norm = {normalize_name(e.name): e for e in entities if e.kind == "character"}
    loc_by_id = {e.id: e for e in entities if e.kind == "location"}

    # Seed running state with the prior book's accumulated facts (continuity).
    states: dict[str, _EntityState] = {
        normalize_name(e.name): _EntityState(prior_facts.get(normalize_name(e.name)))
        for e in entities
    }

    scene_lines: dict[int, str] = {}
    world_hints: list[str] = []
    n = max(1, len(scenes))

    # --- forward pass: walk scenes in order, evolving state ---
    for i, scene in enumerate(scenes):
        if progress:
            progress(0.05 + 0.75 * (i / n))
        scene_text = enrich._scene_text(scene, ch_map)

        present: list[tuple[Entity, _EntityState]] = []
        seen: set[str] = set()
        for c in scene.characters:
            norm = normalize_name(c)
            ent = char_by_norm.get(norm)
            if ent and norm not in seen:
                seen.add(norm)
                present.append((ent, states[norm]))
        loc = loc_by_id.get(scene.location_id)
        if loc:
            lnorm = normalize_name(loc.name)
            present.append((loc, states[lnorm]))

        location_desc = (loc.descriptor or loc.name) if loc else ""
        if present and scene_text:
            log.info(
                "LLM scene %d/%d ch%d mode=%s chars=[%s]",
                i + 1, n, scene.chapter_idx, settings.state_mode,
                ", ".join(e.name for e, _ in present),
            )
            t0 = time.monotonic()
            line, hint = _process_scene(
                scene, scene_text, present, base_prompt, prior_world, location_desc
            )
            log.info("LLM scene %d/%d done %.1fs", i + 1, n, time.monotonic() - t0)
            if hint and len(world_hints) < 12:
                world_hints.append(hint)  # world is synthesized once; a few hints suffice
        else:
            line = ""

        scene_lines[scene.id] = line
        # snapshot the overlay in effect at THIS scene (may be "" early on)
        scene.overlays = {ent.name: st.overlay for ent, st in present if st.overlay}

    # --- reduce: accumulated facts -> stable identity descriptor + world ---
    if progress:
        progress(0.85)
    facts_by_norm = {norm: st.facts for norm, st in states.items()}
    enrich.dedupe_facts(facts_by_norm)
    enrich.strip_transient_facts(facts_by_norm)  # keep events out of the timeless identity
    world = enrich.consolidate(entities, facts_by_norm, world_hints, prior_descs)
    world = prior_world or world
    enrich.fill_blank_descriptors(entities, scenes, chapters, prior_descs, progress)

    # --- assemble the final per-scene prompt (identity verbatim + overlay) ---
    bible_map = {f"{e.kind}:{e.id}": e for e in entities}
    for scene in scenes:
        scene.prompt = prompts.assemble_scene_prompt(
            scene, base_prompt, bible_map, scene_lines.get(scene.id, "")
        )

    log.info("State pass complete (%s mode): %d scenes, %d entities",
             settings.state_mode, len(scenes), len(entities))
    return world
