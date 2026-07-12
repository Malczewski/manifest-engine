"""Stage 2 — split each chapter into meaningful scenes.

Two backends, selected by settings.segmenter:
  * "heuristic" (default, offline): group paragraphs up to a target length.
  * "ollama": ask a local LLM to group paragraphs into scenes and emit
    per-scene metadata as constrained JSON. Any failure falls back to the
    heuristic so the pipeline never hard-stops on a flaky model.

Both operate on paragraph boundaries so scene [start_offset, end_offset)
stays exactly aligned to the whole-book coordinate system from epub_parse.
"""

from __future__ import annotations

import json
import re

import httpx

from ..config import settings
from ..models import Chapter, Scene

_SENT_END = re.compile(r"(?<=[.!?])\s+")


def _paragraphs(text: str) -> list[tuple[int, int, str]]:
    """Return (local_start, local_end, text) for each paragraph in the chapter."""
    out: list[tuple[int, int, str]] = []
    pos = 0
    for part in text.split("\n\n"):
        start = text.find(part, pos)
        if start < 0:
            start = pos
        end = start + len(part)
        pos = end
        if part.strip():
            out.append((start, end, part.strip()))
    return out


def _summary(text: str, limit: int = 200) -> str:
    first = _SENT_END.split(text.strip(), maxsplit=1)[0]
    return (first[:limit]).strip()


def _scene_from_paras(
    scene_id: int, chapter: Chapter, seq: int, paras: list[tuple[int, int, str]]
) -> Scene:
    local_start = paras[0][0]
    local_end = paras[-1][1]
    body = "\n\n".join(p[2] for p in paras)
    return Scene(
        id=scene_id,
        chapter_idx=chapter.idx,
        seq=seq,
        start_offset=chapter.start_offset + local_start,
        end_offset=chapter.start_offset + local_end,
        summary=_summary(body),
        key_action=_summary(body),
    )


def _heuristic(chapter: Chapter, next_id: int) -> list[Scene]:
    paras = _paragraphs(chapter.text)
    if not paras:
        return []
    scenes: list[Scene] = []
    bucket: list[tuple[int, int, str]] = []
    length = 0
    seq = 0
    for p in paras:
        bucket.append(p)
        length += len(p[2])
        if length >= settings.target_scene_chars:
            scenes.append(_scene_from_paras(next_id + len(scenes), chapter, seq, bucket))
            seq += 1
            bucket = []
            length = 0
    if bucket:
        scenes.append(_scene_from_paras(next_id + len(scenes), chapter, seq, bucket))
    return scenes


_LLM_PROMPT = """You are segmenting a book chapter into visual SCENES for illustration.
A scene is a stretch of narration in one place/time with a consistent set of
characters. Group the numbered paragraphs below into consecutive scenes.

Return ONLY JSON of the form:
{{"scenes": [{{"start_para": <int>, "end_para": <int>, "summary": "...",
  "location": "...", "characters": ["..."], "mood": "...",
  "time_of_day": "...", "key_action": "..."}}]}}

CHARACTER RULES — resolve every character to a SPECIFIC identity using the scene's
context, so the same person always gets the same label:
- Use a proper NAME whenever the text gives one (prefer "Josh" over "Cara's father").
- Never output vague references ("he", "she", "the boy", "the man", "her mother").
  Resolve them from context to a specific person: e.g. "Cara's mother" (or her name).
- Expand groups: "her parents" -> the individuals, e.g. ["Cara's mother", "Cara's father"];
  "the children" -> the specific kids present.
- Only list characters actually present in the scene.

Rules: paragraphs are 0-indexed; ranges are inclusive and must be contiguous and
cover every paragraph with no gaps or overlaps; aim for scenes of roughly {target}
characters. Paragraphs:

{paras}
"""


# Paragraphs are batched into windows of about this many characters before being
# sent to the LLM, so a huge single-document chapter never overflows the prompt.
_WINDOW_CHARS = 8000


def _windows(paras: list[tuple[int, int, str]], max_chars: int):
    """Yield contiguous sublists of paragraphs, each up to ~max_chars."""
    window: list[tuple[int, int, str]] = []
    length = 0
    for p in paras:
        if window and length + len(p[2]) > max_chars:
            yield window
            window, length = [], 0
        window.append(p)
        length += len(p[2])
    if window:
        yield window


def _segment_window(
    chapter: Chapter, paras: list[tuple[int, int, str]], seq_start: int
) -> list[Scene]:
    numbered = "\n".join(f"[{i}] {p[2][:500]}" for i, p in enumerate(paras))
    prompt = _LLM_PROMPT.format(target=settings.target_scene_chars, paras=numbered)
    resp = httpx.post(
        f"{settings.ollama_url}/api/generate",
        trust_env=False,  # never proxy localhost
        json={
            "model": settings.ollama_model,
            "prompt": prompt,
            "stream": False,
            "format": "json",
            # qwen3 and other reasoning models: skip the <think> pass so we get
            # clean JSON straight away (ignored by non-thinking models).
            "think": False,
            "options": {"temperature": 0.2},
        },
        timeout=300,
    )
    resp.raise_for_status()
    data = json.loads(resp.json()["response"])
    scenes: list[Scene] = []
    for s in data.get("scenes", []):
        a = max(0, int(s["start_para"]))
        b = min(len(paras) - 1, int(s["end_para"]))
        if b < a:
            continue
        group = paras[a : b + 1]
        scene = _scene_from_paras(
            seq_start + len(scenes), chapter, seq_start + len(scenes), group
        )
        scene.summary = str(s.get("summary", scene.summary))[:400]
        scene.location_id = _slug(str(s.get("location", "")))
        scene.characters = [str(c) for c in s.get("characters", []) if str(c).strip()]
        scene.mood = str(s.get("mood", ""))[:80]
        scene.time_of_day = str(s.get("time_of_day", ""))[:40]
        scene.key_action = str(s.get("key_action", scene.summary))[:400]
        scenes.append(scene)
    return scenes


def _ollama(chapter: Chapter, next_id: int, on_window=None) -> list[Scene]:
    """Segment a chapter, windowing large text so LLM prompts stay bounded."""
    paras = _paragraphs(chapter.text)
    if not paras:
        return []
    scenes: list[Scene] = []
    for window in _windows(paras, _WINDOW_CHARS):
        scenes.extend(_segment_window(chapter, window, next_id + len(scenes)))
        if on_window:
            on_window()  # one window done -> advance segmentation progress
    if not scenes:
        raise ValueError("LLM returned no usable scenes")
    return scenes


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return s[:48]


def segment_chapter(chapter: Chapter, next_id: int, on_window=None) -> list[Scene]:
    if settings.segmenter == "ollama":
        try:
            return _ollama(chapter, next_id, on_window=on_window)
        except Exception as exc:  # noqa: BLE001
            # Flaky model / not running -> don't block, but make it visible so a
            # silent drop to heuristic segmentation isn't mistaken for success.
            from ..log import get_logger

            get_logger("segment").warning(
                "Ollama segmentation failed (%s); using heuristic for chapter %d",
                exc, chapter.idx,
            )
            return _heuristic(chapter, next_id)
    return _heuristic(chapter, next_id)


def _window_count(chapter: Chapter) -> int:
    return max(1, len(list(_windows(_paragraphs(chapter.text), _WINDOW_CHARS))))


def segment_book(chapters: list[Chapter], progress=None) -> list[Scene]:
    """Segment all chapters. `progress(frac)` (0..1) is called as LLM windows
    complete so a long segmentation pass shows movement, not a frozen bar."""
    use_llm = settings.segmenter == "ollama"
    total = (
        sum(_window_count(c) for c in chapters) if use_llm else max(1, len(chapters))
    )
    done = 0

    def tick() -> None:
        nonlocal done
        done += 1
        if progress:
            progress(min(1.0, done / total))

    scenes: list[Scene] = []
    for chapter in chapters:
        scenes.extend(
            segment_chapter(chapter, next_id=len(scenes), on_window=tick if use_llm else None)
        )
        if not use_llm:
            tick()
    return scenes
