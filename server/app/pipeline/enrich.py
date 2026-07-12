"""Stage 3b — enrich the story bible with world context + visual descriptions.

The segmenter gives us entity *names* ("Cara", "the dogs"), but names alone make
the image model fall back on its priors (generic girl, real dog). This stage asks
the LLM, grounded in the book text, for:
  * a WORLD paragraph — setting/genre/art direction, and crucially what unusual
    terms actually mean ("dogs" = alien creatures on Laconia), and
  * a concise VISUAL description per entity (species, age, appearance).

The world string is returned (stored in meta, prepended to every scene prompt);
entity.descriptor fields are filled in place. Any failure degrades gracefully to
empty world + unchanged descriptors so the pipeline never hard-stops.
"""

from __future__ import annotations

import difflib
import json
import re

import httpx

from ..config import settings
from ..models import Entity

# Batch prompt: describe ALL entities together in one call. Describing them
# side-by-side is what keeps them DISTINCT — the model assigns dog features to the
# dogs and human features to the girl, instead of folding the aliens into Cara when
# she's described in isolation.
_PROMPT = """You are building an art bible to ILLUSTRATE a book, from the excerpt.
Return JSON: {{"world": "...", "entities": {{"Name": "visual description"}}}}.
- world: 1-2 sentences of ART DIRECTION only (medium, palette, mood, setting kind).
  If an ordinary word means something unusual (e.g. 'dogs' are alien creatures),
  note it briefly. Do NOT itemize every creature/structure.
- For EACH listed entity, ONE self-contained sentence of PHYSICAL appearance only
  (kind/species, approximate age, build, hair, features, clothing/colors), max 25 words.
- Keep entities DISTINCT: never give one entity another's features. HUMANS are fully
  human (no fur, paws, tails, wings, or hybrid traits) unless the text explicitly
  says the character is non-human. Children must read as young children.
- No "same as"/"like X"; no personality, feelings, role, or plot.
Entities: {names}

Excerpt:
{excerpt}
"""


def _trim(text: str, limit: int = 220) -> str:
    """Trim to a sentence boundary within limit (never mid-word)."""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    cut = text[:limit]
    end = max(cut.rfind(". "), cut.rfind("; "))
    return (cut[: end + 1] if end > 40 else cut.rsplit(" ", 1)[0]).rstrip(",; ") + "."


# Small models ignore the "no cross-reference" instruction and still emit e.g.
# "Same as Cara's mother, ...". We detect that deterministically and substitute
# the referenced entity's real description (they ARE the same person).
_CROSSREF = re.compile(
    r"\b(?:same as|identical to|similar to|same appearance as|"
    r"same as (?:described )?for|matching(?: that of)?)\s+"
    r"([A-Za-z'’][A-Za-z'’.\- ]*)",
    re.IGNORECASE,
)


_ONE_PROMPT = """Describe ONLY the physical appearance of "{name}" for illustrating them,
based on the text below. One sentence, max 25 words: kind/species, approximate age,
build, hair, distinctive features, clothing/colors. IGNORE every other person, animal
or creature in the text — never give "{name}" their features. A human is fully human
(no fur, paws, tails, wings, or hybrid traits) unless the text explicitly says
otherwise. No "same as"/"like", no personality. If detail is missing, give a plausible
ordinary appearance that fits the setting.

Text:
{excerpt}

Return JSON: {{"description": "..."}}.
"""


def _describe_one(name: str, excerpt: str) -> str:
    """Fallback single-entity description for when the batch pass left an entity
    blank or cross-referenced."""
    try:
        data = _ollama_json(_ONE_PROMPT.format(name=name, excerpt=excerpt[:6000]))
        return _trim(str(data.get("description", "")), 200)
    except Exception:
        return ""


def _resolve_desc(name: str, descs: dict[str, str], seen: set[str] | None = None) -> str:
    """Replace a 'same as X' description with X's real description (chains guarded).
    If X isn't a known entity, keep the text (probably a simile like 'eyes like the sea')."""
    seen = seen or set()
    d = (descs.get(name) or "").strip()
    if name in seen:
        return d
    seen.add(name)
    m = _CROSSREF.search(d)
    if not m:
        return d
    ref = m.group(1).strip().rstrip(".,;'’ ").lower()
    target = ref if ref in descs and ref != name else None
    if not target:
        cand = difflib.get_close_matches(ref, [k for k in descs if k != name], n=1, cutoff=0.6)
        target = cand[0] if cand else None
    if target:
        resolved = _resolve_desc(target, descs, seen)
        if resolved and not _CROSSREF.search(resolved):
            return resolved
    return d


def _ollama_json(prompt: str) -> dict:
    resp = httpx.post(
        f"{settings.ollama_url}/api/generate",
        trust_env=False,
        json={
            "model": settings.ollama_model,
            "prompt": prompt,
            "stream": False,
            "format": "json",
            "think": False,
            "options": {"temperature": 0.3},
        },
        timeout=240,
    )
    resp.raise_for_status()
    return json.loads(resp.json()["response"])


def enrich(entities: list[Entity], excerpt: str, progress=None) -> str:
    """Describe all entities in ONE batch call (keeps them distinct), clean up any
    'same as X' cross-references, and re-describe individually only the few that
    come back blank or still cross-referenced. Returns a short world string."""
    names = sorted({e.name for e in entities})
    if not names or not excerpt.strip():
        return ""
    if progress:
        progress(0.1)
    try:
        data = _ollama_json(_PROMPT.format(names=", ".join(names), excerpt=excerpt))
    except Exception:
        return ""  # LLM unavailable -> keep going with names only

    world = _trim(str(data.get("world", "")), 320)
    descs = {str(k).lower(): str(v) for k, v in data.get("entities", {}).items()}
    descs = {k: _resolve_desc(k, descs) for k in descs}
    keys = list(descs.keys())
    for ent in entities:
        match = difflib.get_close_matches(ent.name.lower(), keys, n=1, cutoff=0.5)
        if not match:
            match = [k for k in keys if ent.name.lower() in k or k in ent.name.lower()]
        ent.descriptor = _trim(descs[match[0]], 200) if match else ""

    bad = [e for e in entities if not e.descriptor or _CROSSREF.search(e.descriptor)]
    for i, ent in enumerate(bad):
        if progress:
            progress(0.5 + 0.5 * (i / max(1, len(bad))))
        desc = _describe_one(ent.name, excerpt)
        ent.descriptor = desc if desc and not _CROSSREF.search(desc) else ""
    return world
