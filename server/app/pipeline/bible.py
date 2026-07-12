"""Stage 3 — assemble the story "bible": canonical characters and locations.

These entities are the consistency anchors: each gets one reference image and one
enriched description that later scenes reuse. The segmenter refers to the same
character by varying names ("Reyes", "Captain Reyes"; "Parents", "Her parents"),
so we normalize names (dropping titles/possessives/articles) and merge variants
into one canonical entity — otherwise we'd describe and seed the same person
twice. Name resolution used here is shared with prompts.py via normalize_name.
"""

from __future__ import annotations

import re

from ..models import Entity, Scene

# Words stripped when normalizing a name so variants collapse together.
_STOPWORDS = {
    "the", "a", "an", "her", "his", "their", "its", "my", "your", "our",
    "captain", "mr", "mrs", "ms", "miss", "dr", "sir", "lady", "lord", "old",
    "young", "young'un",
}


def normalize_name(name: str) -> str:
    """Canonical key for matching entity name variants."""
    words = re.sub(r"[^a-z0-9 ]", " ", name.lower()).split()
    kept = [w for w in words if w not in _STOPWORDS]
    return " ".join(kept or words)  # never return empty if the name was all stopwords


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return s[:48]


_ARTICLES = {"the", "a", "an"}
_POSSESSIVE_PRONOUNS = {"her", "his", "their", "its", "my", "your", "our"}
_RELATION_WORDS = {
    "mother", "father", "mom", "dad", "mum", "parent", "parents", "brother",
    "sister", "son", "daughter", "grandmother", "grandfather", "grandma",
    "grandpa", "uncle", "aunt", "baby", "babies", "child", "children", "kid", "kids",
}
# Interchangeable role/collective nouns — a different individual each time, so they
# should NOT be persistent bible characters (the scene composer handles them locally).
_GENERIC_PEOPLE = {
    "soldier", "soldiers", "guard", "guards", "student", "students", "man", "woman",
    "men", "women", "boy", "girl", "boys", "girls", "people", "crowd", "villager",
    "villagers", "adult", "adults", "person", "figure", "figures", "stranger",
    "strangers", "someone", "somebody", "officer", "officers", "worker", "workers",
    "citizen", "citizens", "prisoner", "prisoners", "passenger", "passengers",
    "scientist", "scientists", "colonist", "colonists", "marine", "marines", "crew",
}


def _is_specific_character(name: str) -> bool:
    """True only for characters with a concrete, consistent identity worth a bible
    entry: a proper name, or a possessive proper owner ("Cara's mother"). Rejects
    vague pronoun-relations ("her parents"), generic groups ("the babies"), and
    interchangeable roles ("the soldiers"). Creatures/species (e.g. "the dogs")
    are kept — they read as recurring, designed beings, not incidental extras."""
    tokens = name.split()
    if not tokens:
        return False
    low = [re.sub(r"[^a-z0-9]", "", t.lower()) for t in tokens]
    if low[0] in _POSSESSIVE_PRONOUNS:  # "her mother", "his father", "her parents"
        return False
    core = tokens[1:] if low[0] in _ARTICLES else tokens
    core_low = [re.sub(r"[^a-z0-9]", "", t.lower()) for t in core]
    if not core:
        return False
    if any("'s" in t.lower() and t[:1].isupper() for t in core):  # "Cara's ..."
        return True
    for t, cl in zip(core, core_low):  # a real proper-name token
        if t[:1].isupper() and cl not in _GENERIC_PEOPLE and cl not in _RELATION_WORDS:
            return True
    if all(cl in _GENERIC_PEOPLE or cl in _RELATION_WORDS for cl in core_low):
        return False  # purely generic/relational with no name -> not a bible entity
    return True  # e.g. "dogs", "dog-like creatures" -> keep as a creature entity


def build_bible(scenes: list[Scene], min_scenes: int = 2) -> list[Entity]:
    """Collect the entities that need cross-scene CONSISTENCY: characters and
    locations that appear in at least `min_scenes` distinct scenes.

    One-off characters/places are intentionally excluded — they appear once, so
    they need no persistent description; the scene composer places them inline.
    This keeps the bible small and focused (and enrichment fast). Name variants
    are merged by normalized name, keeping the longest surface form as canonical.
    Descriptors start empty and are filled by the enrichment stage.
    """
    counts: dict[str, dict[str, int]] = {"character": {}, "location": {}}
    display: dict[str, dict[str, str]] = {"character": {}, "location": {}}

    def note(kind: str, name: str) -> None:
        norm = normalize_name(name)
        if not norm:
            return
        counts[kind][norm] = counts[kind].get(norm, 0) + 1
        cur = display[kind].get(norm)
        if cur is None or len(name) > len(cur):
            display[kind][norm] = name

    for scene in scenes:
        seen: set[str] = set()
        for name in scene.characters:  # count each character once per scene
            norm = normalize_name(name)
            if norm and norm not in seen:
                seen.add(norm)
                note("character", name)
        if scene.location_id:
            note("location", scene.location_id.replace("_", " "))

    entities: list[Entity] = []
    for kind in ("character", "location"):
        for norm, cnt in counts[kind].items():
            if cnt < min_scenes:
                continue
            name = display[kind][norm]
            if kind == "character" and not _is_specific_character(name):
                continue  # drop vague/generic/interchangeable references
            entities.append(Entity(id=_slug(name), kind=kind, name=name, descriptor=""))
    return entities
