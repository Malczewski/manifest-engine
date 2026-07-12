"""Stage 2b — consolidate entity name variants across the whole book (LLM).

The segmenter names the same person many ways ("Reyes"/"Captain Reyes";
"mother"/"her mother"/"Cara's mother"). Merging by string normalization can't
resolve "her" -> "Cara" — that's coreference — so we ask the LLM once per book to
map every extracted name to a single canonical name, then rewrite the scenes in
place. Everything downstream (bible, enrichment, prompts, seeds) then sees one
consistent name per character, which fixes both duplicate/garbled descriptions
and cross-book/scene consistency. No-ops if the LLM is unavailable.
"""

from __future__ import annotations

import json
import re

import httpx

from ..config import settings
from ..models import Scene

_PROMPT = """Consolidate these entity names from ONE book. Merge two names ONLY when
you are CONFIDENT they denote the SAME individual. Be conservative — when in doubt,
keep them separate.
Merge:
- the same proper name with/without a title or role: "Captain Reyes" = "Reyes".
- a pronoun/bare relation word ("her mother", "mother") into a named form
  ("Cara's mother") ONLY IF exactly ONE such owner exists in the list (unambiguous).
Do NOT merge:
- two different owners' relations: "Cara's father" and "Ana's father" are DIFFERENT.
- an ambiguous bare relation ("father", "her father") when several owners exist —
  leave it as-is.
Map EACH input name to ONE canonical name (prefer a proper name). EVERY input name
must appear as a key in the output.

Example input: ["Reyes","Captain Reyes","her mother","Cara's mother","mother","Ana's father","Cara's father"]
Example output: {{"Reyes":"Captain Reyes","Captain Reyes":"Captain Reyes",
"her mother":"Cara's mother","Cara's mother":"Cara's mother","mother":"Cara's mother",
"Ana's father":"Ana's father","Cara's father":"Cara's father"}}

Return JSON: {{"characters": {{"input":"canonical"}}, "locations": {{"input":"canonical"}}}}.

Characters: {chars}
Locations: {locs}
"""


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:48]


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
            "options": {"temperature": 0.1},
        },
        timeout=180,
    )
    resp.raise_for_status()
    return json.loads(resp.json()["response"])


def canonicalize_scenes(scenes: list[Scene]) -> None:
    """Rewrite scene.characters and scene.location_id to canonical names."""
    if settings.segmenter != "ollama":
        return
    chars = sorted({c for s in scenes for c in s.characters if c.strip()})
    locs = sorted({s.location_id.replace("_", " ") for s in scenes if s.location_id})
    if not chars and not locs:
        return
    try:
        data = _ollama_json(_PROMPT.format(chars=chars, locs=locs))
    except Exception:
        return  # LLM unavailable -> leave names as-is (string dedup still applies)

    cmap = {str(k): str(v) for k, v in data.get("characters", {}).items()}
    lmap = {str(k): str(v) for k, v in data.get("locations", {}).items()}
    for s in scenes:
        merged: list[str] = []
        for c in s.characters:
            canon = cmap.get(c, c)
            if canon and canon not in merged:
                merged.append(canon)
        s.characters = merged
        if s.location_id:
            readable = s.location_id.replace("_", " ")
            s.location_id = _slug(lmap.get(readable, readable))
