"""Stage 5b — LLM-compose a focused prompt line for each scene.

Prepending the whole world paragraph to every scene made the model paint every
world prop (alien dogs, stick moons, structures) into moments that don't contain
them. Instead we ask the LLM to write ONE concise line describing only what is
visible in this scene.

Character STABLE appearance (hair, species, marks) is NOT composed here — the
caller appends the canonical descriptions verbatim so identity (and thus the
per-cast seed consistency) stays stable across scenes.

Scene-SPECIFIC state (current outfit, visible injury, unusual lighting) IS
handled here: when the raw scene body is provided, the LLM can read it and
incorporate anything transient that the structured fields (action, mood, tod)
don't capture.

Falls back to None on any failure so the caller can use a mechanical prompt.
"""

from __future__ import annotations

from . import llm
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
- The characters' stable appearance (hair, eyes, species) comes from their bible
  descriptions appended separately — do NOT reinvent or contradict it. Instead focus
  on their current action and, if the scene text mentions a specific outfit, prop, or
  visible condition, incorporate that naturally into the prompt.
- Do NOT add creatures, buildings, moons, crowds, or world details absent from
  this specific moment. Do NOT describe faces in isolation.
- Concrete and visual; no narration, backstory, or lists.

Art style: {style}
World (background context only — do NOT dump into the prompt): {world}
Location: {location}
Characters present: {names}
Action: {action}
Mood: {mood}. Time of day: {tod}.
{scene_context}"""


def compose_scene_line(
    *,
    style: str,
    world: str,
    location: str,
    names: list[str],
    action: str,
    mood: str,
    time_of_day: str,
    scene_body: str = "",
) -> str | None:
    if not settings.compose_prompts or not settings.use_llm:
        return None

    scene_context = ""
    if scene_body.strip():
        # Trim to a reasonable window — we want the LLM to pick up current
        # clothing/state without the whole chapter overwhelming the prompt.
        scene_context = f"\nScene text (use only for current clothing / visible state):\n{scene_body[:800]}"

    prompt = _PROMPT.format(
        style=style or "cinematic illustration",
        world=world[:400],
        location=location or "unspecified",
        names=", ".join(names) or "none",
        action=action or "",
        mood=mood or "",
        tod=time_of_day or "",
        scene_context=scene_context,
    )
    try:
        line = str(llm.call_json(prompt, temperature=0.4).get("prompt", "")).strip()
        return line or None
    except Exception:
        return None
