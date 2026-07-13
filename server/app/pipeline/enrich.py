"""Stage 3b — enrich the story bible with world context + visual descriptions.

The segmenter gives us entity *names* ("Cara", "the dogs"), but names alone make
the image model fall back on its priors (generic girl, real dog). This stage asks
the LLM, grounded in the book text, for:
  * a WORLD paragraph — setting/genre/art direction, and crucially what unusual
    terms actually mean ("dogs" = alien creatures on Laconia), and
  * a STABLE PHYSICAL description per entity (species, age, features, marks).

WHOLE-BOOK ANALYSIS (map-reduce)
--------------------------------
We do NOT sample the book. The entire text is walked in chunks (settings.
enrich_chunk_chars). This scales to any book on any backend — a whole novel does
not fit a local model's context window, and even on Gemini's 1M window a single
giant prompt loses detail buried in the middle ("lost in the middle").

  * MAP: for each chunk, extract the STABLE physical FACTS of whichever bible
    entities appear in it (short bullet facts, not a finished sentence).
  * REDUCE: for each entity, merge the facts accumulated across ALL chunks into
    one coherent descriptor, resolving contradictions.

"Stable" = things that don't change scene to scene: hair colour, species, build,
scars/tattoos. Clothing and emotional state are excluded — those are scene-
specific and handled by the compose step.

For book series: the caller may supply prior_descs (entity name → existing
descriptor from an earlier book). Those seed the REDUCE step so a recurring
character is extended/refined rather than reinvented; a later book can still
override the look when its own facts contradict the earlier description.
"""

from __future__ import annotations

import difflib
import re

from . import llm
from .bible import normalize_name
from ..config import settings
from ..models import Chapter, Entity, Scene

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

# MAP: pull raw stable facts for the entities present in one chunk. We ask for
# short factual bullets (not a finished sentence) so the reduce step can merge
# them; describing all present entities together keeps them distinct (gotcha #1).
_FACTS_PROMPT = """From the book excerpt below, extract STABLE PHYSICAL FACTS about each
listed entity that ACTUALLY APPEARS in this excerpt.
Return JSON: {{"world_hint": "...", "entities": [{{"name": "...", "facts": ["...", "..."]}}]}}.

STABLE facts only — permanent physical attributes an illustrator needs:
  Characters: species/humanoid type, approximate age, build/height, hair (colour,
  texture, style), eye colour, skin tone, distinctive PERMANENT marks (scars,
  tattoos, freckles, non-human features such as horns, tails, unusual ears).
  Locations: architecture, scale, materials, defining spatial features.
DO NOT record (they change scene to scene): current outfit/clothing, emotional
  state, actions, personality, role, or anything temporary.
Rules:
- One short fact per array item (e.g. "red curly hair", "scar over left eyebrow").
- Only facts stated or strongly implied by THIS excerpt. If an entity appears but
  no stable fact is given, return it with an empty facts array. Omit entities not
  present here.
- Keep entities DISTINCT — never give one entity another's features. A human is
  fully human (no fur, paws, tails, wings) unless the text explicitly says otherwise.
- world_hint: a few words on medium/palette/setting mood if evident, else "".

Entities to look for: {names}

Excerpt:
{excerpt}
"""

_FACTS_SCHEMA = {
    "type": "object",
    "properties": {
        "world_hint": {"type": "string"},
        "entities": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "facts": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["name", "facts"],
            },
        },
    },
    "required": ["entities"],
}

# REDUCE: merge each entity's accumulated facts (+ optional prior-book descriptor)
# into one final descriptor, and synthesise the world string from the hints.
_CONSOLIDATE_PROMPT = """You are finalising an art bible to ILLUSTRATE a book (or series).
For each entity you are given the STABLE physical facts observed throughout the whole
book, and (sometimes) a description from an earlier book in the series.
Return JSON: {{"world": "...", "entities": [{{"name": "...", "description": "..."}}]}}.

WORLD: 1-2 sentences of ART DIRECTION only (medium, palette, mood, kind of setting),
synthesised from the world hints below. Do NOT list specific creatures/landmarks; if
you must name a distinctive thing, say what it ACTUALLY is (e.g. "dogs" = alien beasts).

For each entity, write description = ONE concrete sentence (15-30 words) of STABLE
PHYSICAL ATTRIBUTES ONLY (species, age, build, hair, eyes, skin, permanent marks):
- Merge ALL the observed facts into one coherent look. If facts conflict, prefer the
  more specific / more frequently attested one.
- If a prior-book description is given, EXTEND/REFINE it with the new facts; only
  override it where the new facts clearly contradict it. Do not reinvent it.
- Where facts are thin, choose plausible concrete details that fit and state them
  definitely — never vague filler ("practical clothing", "standard build").
- NEVER include clothing, emotional state, personality, role, or plot.
- Keep entities distinct; a human is fully human unless stated otherwise.
- No "same as"/"like X".

World hints observed across the book: {world_hints}

Entities (name — prior description — observed facts):
{entity_blocks}
"""

_ENRICH_SCHEMA = {
    "type": "object",
    "properties": {
        "world": {"type": "string"},
        "entities": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                },
                "required": ["name", "description"],
            },
        },
    },
    "required": ["world", "entities"],
}

_ONE_PROMPT = """Describe ONLY the stable physical appearance of "{name}" for illustrating them,
based on the text. One concrete sentence, 15-30 words: species/humanoid type, age range,
build, hair (colour + style), eye colour, skin tone, distinctive permanent marks (scars,
tattoos, non-human features). Do NOT describe clothing or emotional state (those change
between scenes). Where the text is silent, choose plausible concrete details and state
them definitely. IGNORE other characters — never give "{name}" their features. A human
is fully human unless the text says otherwise. No "same as"/"like", no personality.

Text:
{excerpt}

Return JSON: {{"description": "..."}}.
"""


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------

def _scene_text(scene: Scene, ch_map: dict[int, Chapter]) -> str:
    ch = ch_map.get(scene.chapter_idx)
    if not ch:
        return ""
    lo = max(0, scene.start_offset - ch.start_offset)
    hi = min(len(ch.text), scene.end_offset - ch.start_offset)
    return ch.text[lo:hi].strip()


def _book_chunks(
    entities: list[Entity],
    scenes: list[Scene],
    chapters: list[Chapter],
    max_chars: int,
):
    """Walk the WHOLE book in order, yielding (chunk_text, present_names).

    Scenes are accumulated until the chunk reaches max_chars; present_names is
    the set of bible-entity display names that appear in that chunk (so the map
    prompt only asks about entities actually there). Reading via scenes keeps the
    chunk boundaries aligned to narrative units and lets us tag entities cheaply.
    """
    ch_map = {c.idx: c for c in chapters}
    char_by_norm = {normalize_name(e.name): e for e in entities if e.kind == "character"}
    loc_by_id = {e.id: e for e in entities if e.kind == "location"}

    buf: list[str] = []
    names: set[str] = set()
    length = 0

    def flush():
        return "\n\n".join(buf), set(names)

    for scene in scenes:
        text = _scene_text(scene, ch_map)
        if not text:
            continue
        if buf and length + len(text) > max_chars:
            yield flush()
            buf, names, length = [], set(), 0
        buf.append(text)
        length += len(text)
        for c in scene.characters:
            ent = char_by_norm.get(normalize_name(c))
            if ent:
                names.add(ent.name)
        loc = loc_by_id.get(scene.location_id)
        if loc:
            names.add(loc.name)
    if buf:
        yield flush()


def _entity_excerpt(
    entity: Entity, scenes: list[Scene], chapters: list[Chapter], max_chars: int = 4000
) -> str:
    """Text from the scenes where this specific entity appears (fallback path)."""
    ch_map = {c.idx: c for c in chapters}
    norm = normalize_name(entity.name)
    if entity.kind == "location":
        relevant = [s for s in scenes if s.location_id == entity.id]
    else:
        relevant = [s for s in scenes if any(normalize_name(c) == norm for c in s.characters)]
    parts: list[str] = []
    total = 0
    for scene in relevant:
        text = _scene_text(scene, ch_map)
        if not text:
            continue
        if total + len(text) > max_chars:
            remaining = max_chars - total
            if remaining > 200:
                parts.append(text[:remaining])
            break
        parts.append(text)
        total += len(text)
    return "\n[...]\n".join(parts)


def _trim(text: str, limit: int = 220) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    cut = text[:limit]
    end = max(cut.rfind(". "), cut.rfind("; "))
    return (cut[: end + 1] if end > 40 else cut.rsplit(" ", 1)[0]).rstrip(",; ") + "."


# ---------------------------------------------------------------------------
# Cross-reference cleanup (small models still emit "same as X")
# ---------------------------------------------------------------------------

_CROSSREF = re.compile(
    r"\b(?:same as|identical to|similar to|same appearance as|"
    r"same as (?:described )?for|matching(?: that of)?)\s+"
    r"([A-Za-z''][A-Za-z''.\- ]*)",
    re.IGNORECASE,
)


def _resolve_desc(name: str, descs: dict[str, str], seen: set[str] | None = None) -> str:
    seen = seen or set()
    d = (descs.get(name) or "").strip()
    if name in seen:
        return d
    seen.add(name)
    m = _CROSSREF.search(d)
    if not m:
        return d
    ref = m.group(1).strip().rstrip(".,;'' ").lower()
    target = ref if ref in descs and ref != name else None
    if not target:
        cand = difflib.get_close_matches(ref, [k for k in descs if k != name], n=1, cutoff=0.6)
        target = cand[0] if cand else None
    if target:
        resolved = _resolve_desc(target, descs, seen)
        if resolved and not _CROSSREF.search(resolved):
            return resolved
    return d


def _describe_one(
    name: str, entity: Entity, scenes: list[Scene], chapters: list[Chapter]
) -> str:
    """Fallback per-entity description from that entity's own scenes."""
    excerpt = _entity_excerpt(entity, scenes, chapters, max_chars=4000)
    if not excerpt:
        return ""
    try:
        data = llm.call_json(_ONE_PROMPT.format(name=name, excerpt=excerpt[:6000]), temperature=0.3)
        return _trim(str(data.get("description", "")), 200)
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Map phase
# ---------------------------------------------------------------------------

def _extract_facts(chunk_text: str, present_names: list[str]) -> tuple[dict[str, list[str]], str]:
    """Map one chunk -> {lowercased entity name: [facts]} + a world hint."""
    if not present_names or not chunk_text.strip():
        return {}, ""
    data = llm.call_json(
        _FACTS_PROMPT.format(names=", ".join(sorted(present_names)), excerpt=chunk_text),
        schema=_FACTS_SCHEMA,
        temperature=0.2,
    )
    out: dict[str, list[str]] = {}
    for e in data.get("entities", []):
        if not isinstance(e, dict) or not e.get("name"):
            continue
        facts = [str(f).strip() for f in e.get("facts", []) if str(f).strip()]
        if facts:
            out[str(e["name"]).lower()] = facts
    return out, str(data.get("world_hint", "")).strip()


# ---------------------------------------------------------------------------
# Reduce phase
# ---------------------------------------------------------------------------

def _consolidate(
    entities: list[Entity],
    facts_by_norm: dict[str, list[str]],
    world_hints: list[str],
    prior_descs: dict[str, str],
) -> str:
    """Reduce accumulated facts (+ prior descriptions) into final descriptors.

    Fills entity.descriptor in place and returns the synthesised world string.
    """
    blocks: list[str] = []
    for ent in entities:
        norm = normalize_name(ent.name)
        facts = facts_by_norm.get(norm, [])
        prior = prior_descs.get(norm, "")
        fact_str = "; ".join(facts) if facts else "(none observed)"
        blocks.append(f"- {ent.name} — prior: {prior or '(none)'} — facts: {fact_str}")

    hint_str = "; ".join(h for h in world_hints if h)[:600] or "(none)"
    try:
        data = llm.call_json(
            _CONSOLIDATE_PROMPT.format(world_hints=hint_str, entity_blocks="\n".join(blocks)),
            schema=_ENRICH_SCHEMA,
            temperature=0.3,
        )
    except Exception:
        return ""

    world = _trim(str(data.get("world", "")), 320)
    descs: dict[str, str] = {
        str(e.get("name", "")).lower(): str(e.get("description", ""))
        for e in data.get("entities", [])
        if isinstance(e, dict) and e.get("name")
    }
    descs = {k: _resolve_desc(k, descs) for k in descs}
    keys = list(descs.keys())
    for ent in entities:
        match = difflib.get_close_matches(ent.name.lower(), keys, n=1, cutoff=0.5)
        if not match:
            match = [k for k in keys if ent.name.lower() in k or k in ent.name.lower()]
        ent.descriptor = _trim(descs[match[0]], 200) if match else ""
    return world


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def enrich(
    entities: list[Entity],
    progress=None,
    *,
    scenes: list[Scene],
    chapters: list[Chapter],
    prior_descs: dict[str, str] | None = None,
) -> str:
    """Whole-book map-reduce enrichment. Fills entity.descriptor in place and
    returns a short world string.

    Args:
        entities:    bible entities to describe
        scenes:      all book scenes (chunk boundaries + entity presence)
        chapters:    all book chapters (source of scene text)
        prior_descs: normalized-name -> descriptor from an earlier book, used as
                     seed context in the reduce step for cross-book continuity
    """
    if not entities or not settings.use_llm:
        return ""
    prior_descs = prior_descs or {}

    chunks = list(_book_chunks(entities, scenes, chapters, settings.enrich_chunk_chars))
    if not chunks:
        return ""

    # --- MAP: accumulate stable facts per entity across the whole book ---
    facts_by_norm: dict[str, list[str]] = {}
    world_hints: list[str] = []
    name_to_norm = {e.name.lower(): normalize_name(e.name) for e in entities}
    for i, (chunk_text, present) in enumerate(chunks):
        if progress:
            progress(0.05 + 0.75 * (i / len(chunks)))
        try:
            chunk_facts, hint = _extract_facts(chunk_text, sorted(present))
        except Exception:
            continue  # a flaky chunk shouldn't sink the whole pass
        if hint:
            world_hints.append(hint)
        for lname, facts in chunk_facts.items():
            # map the model's returned name back to a canonical bible key
            norm = name_to_norm.get(lname) or normalize_name(lname)
            facts_by_norm.setdefault(norm, []).extend(facts)

    # de-dupe facts per entity while preserving order
    for norm, facts in facts_by_norm.items():
        seen: set[str] = set()
        deduped = []
        for f in facts:
            key = f.lower()
            if key not in seen:
                seen.add(key)
                deduped.append(f)
        facts_by_norm[norm] = deduped

    # --- REDUCE: merge facts (+ prior descriptions) into final descriptors ---
    if progress:
        progress(0.85)
    world = _consolidate(entities, facts_by_norm, world_hints, prior_descs)

    # --- fallback: entities that came back blank or cross-referenced ---
    bad = [e for e in entities if not e.descriptor or _CROSSREF.search(e.descriptor)]
    for i, ent in enumerate(bad):
        if progress:
            progress(0.9 + 0.1 * (i / max(1, len(bad))))
        # prefer prior description over an empty result before re-describing
        prior = prior_descs.get(normalize_name(ent.name), "")
        desc = _describe_one(ent.name, ent, scenes, chapters) or prior
        ent.descriptor = desc if desc and not _CROSSREF.search(desc) else ""

    return world
