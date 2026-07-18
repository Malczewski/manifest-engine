"""Stage 5b — LLM-compose a focused prompt line for each scene.

Prepending the whole world paragraph to every scene made the model paint every
world prop (alien dogs, stick moons, structures) into moments that don't contain
them. Instead we ask the LLM to write ONE concise line describing only what is
visible in this scene.

Neither stable appearance (hair, species, marks) NOR scene-specific state
(current outfit, injury) is composed here — the caller appends the canonical
identity descriptor and the per-scene overlay separately. This line is purely the
action/setting of the moment, so it never fights the appended appearance text.

Used as the compose step of the per_scene state mode, and as the fallback prompt
line when there is no state pass. Falls back to None on any failure.
"""

from __future__ import annotations

from . import llm
from ..config import settings
from ..log import get_logger

log = get_logger("compose")

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
- Describe ONLY the action, pose, and setting of this moment. Do NOT describe any
  character's appearance, clothing, or physical features — those are appended
  separately, and repeating them here causes conflicts. Just place the named
  characters in the scene doing the action.
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
        # Trim to a reasonable window — context for the action/setting only.
        scene_context = f"\nScene text (for the action/setting; do NOT copy appearance):\n{scene_body[:800]}"

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
    except Exception as exc:
        log.warning("compose LLM call failed: %s", exc)
        return None


_REPHRASE_PROMPT = """An image-generation model REJECTED this prompt — it could not
generate an image from it and asked to rephrase. Rewrite the prompt so it will render,
WITHOUT losing the scene: keep the same characters and their described appearance, the
setting, and the action. Make it read clearly as a STYLIZED ILLUSTRATION (not a photo of
real people), simplify awkward or repetitive phrasing, and remove anything that might
trip a content filter. Keep it under 80 words. Return JSON: {{"prompt": "..."}}.

Rejected prompt:
{original}
"""


def rephrase_image_prompt(original: str) -> str | None:
    """Ask the LLM to rewrite an image prompt the image model refused to render.
    Returns a new prompt, or None if unavailable/unchanged."""
    if not settings.use_llm or not original.strip():
        return None
    try:
        out = llm.call_json(_REPHRASE_PROMPT.format(original=original), temperature=0.5)
        new = str(out.get("prompt", "")).strip()
        return new or None
    except Exception as exc:
        log.warning("rephrase LLM call failed: %s", exc)
        return None


_SANITIZE_PROMPT = """An image-generation model REFUSED this prompt for violating its
content policy (explicit / sexual content). Rewrite it into a SAFE, policy-compliant
image prompt that will be accepted, changing AS LITTLE as possible:
- Keep the SAME scene, the SAME characters (and their described appearance), the setting,
  the action, and the emotional tone. Do NOT relocate it, swap characters, or turn it
  into a different scene.
- Remove ONLY the explicit elements: no nudity, no sexual or graphic content. Make any
  intimacy TASTEFUL and IMPLIED instead — e.g. a tender embrace with the figures
  obscured by steam or shadow, modest framing — while staying true to the moment.
- Write it as one natural, fluent image prompt (no leftover fragments), under 80 words.
Return JSON: {{"prompt": "..."}}.

Refused prompt:
{original}
"""


def sanitize_image_prompt(original: str) -> str | None:
    """Ask the LLM to rewrite a prompt the image model refused on CONTENT-POLICY
    grounds into a tasteful, non-explicit version — keeping the scene, characters and
    tone, softening only the explicit parts. Returns None if unavailable/unchanged."""
    if not settings.use_llm or not original.strip():
        return None
    try:
        out = llm.call_json(_SANITIZE_PROMPT.format(original=original), temperature=0.4)
        new = str(out.get("prompt", "")).strip()
        return new or None
    except Exception as exc:
        log.warning("sanitize LLM call failed: %s", exc)
        return None
