"""Stage 5b — LLM-compose a focused prompt line for each scene.

Prepending the whole world paragraph to every scene made the model paint every
world prop (alien dogs, stick moons, structures) into moments that don't contain
them. Instead we ask the LLM to write ONE concise line describing only what is
visible in this scene. Character *appearance* is NOT composed here — the caller
appends the canonical descriptions verbatim so identity (and thus the per-cast
seed consistency) stays stable across scenes.

Falls back to None on any failure so the caller can use a mechanical prompt.
"""

from __future__ import annotations

import json

import httpx

from ..config import settings

_PROMPT = """Write ONE image-generation prompt for a single illustrated book scene.
Return JSON: {{"prompt": "..."}}.
Rules:
- One vivid sentence, at most 40 words, describing ONLY what is visible in THIS scene.
- Show the listed characters performing the action, in the location.
- Attribute every action and body part to an explicit subject by name. NEVER merge
  one being's features into another. NEVER give a human character animal features
  (ears, paws, fur, whiskers, tails) — those belong only to animals/creatures.
- Treat each listed character as a DISTINCT person and keep them visually distinct.
  Interpret relationship references using only the people present in THIS scene
  (e.g. 'her father' means the father of the viewpoint character listed here).
- Keep humans anatomically human and realistic.
- Do NOT add creatures, buildings, moons, crowds, or world details that are not part
  of this specific moment. Do NOT describe the characters' faces or clothing.
- Concrete and visual; no narration, backstory, or lists.

Art style: {style}
World (background context only — do NOT dump it into the prompt): {world}
Location: {location}
Characters present: {names}
Action: {action}
Mood: {mood}. Time of day: {tod}.
"""


def compose_scene_line(
    *,
    style: str,
    world: str,
    location: str,
    names: list[str],
    action: str,
    mood: str,
    time_of_day: str,
) -> str | None:
    if not settings.compose_prompts or settings.segmenter != "ollama":
        return None
    prompt = _PROMPT.format(
        style=style or "cinematic illustration",
        world=world[:400],
        location=location or "unspecified",
        names=", ".join(names) or "none",
        action=action or "",
        mood=mood or "",
        tod=time_of_day or "",
    )
    try:
        resp = httpx.post(
            f"{settings.ollama_url}/api/generate",
            trust_env=False,
            json={
                "model": settings.ollama_model,
                "prompt": prompt,
                "stream": False,
                "format": "json",
                "think": False,
                "options": {"temperature": 0.4},
            },
            timeout=120,
        )
        resp.raise_for_status()
        line = str(json.loads(resp.json()["response"]).get("prompt", "")).strip()
        return line or None
    except Exception:
        return None
