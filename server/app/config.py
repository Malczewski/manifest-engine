"""Runtime configuration, driven entirely by environment variables.

Everything has a sensible default so the server runs fully offline out of the
box: the STUB image backend and the HEURISTIC segmenter need no external
services. Point IMAGE_BACKEND / SEGMENTER at the real engines when available.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

_SERVER_ROOT = Path(__file__).resolve().parent.parent


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class Settings:
    # --- storage ---
    data_dir: Path = Path(_env("DATA_DIR", str(_SERVER_ROOT / "data")))

    # --- image generation ---
    # "drawthings" (real) | "stub" (offline placeholder for tests)
    image_backend: str = _env("IMAGE_BACKEND", "drawthings")
    drawthings_url: str = _env("DRAWTHINGS_URL", "http://127.0.0.1:7860")
    image_width: int = _env_int("IMAGE_WIDTH", 768)
    image_height: int = _env_int("IMAGE_HEIGHT", 512)
    image_steps: int = _env_int("IMAGE_STEPS", 12)
    # DT model checkpoint filename (from /sdapi/v1/options "model"); blank = whatever
    # is currently selected in the Draw Things app.
    image_model: str = _env("IMAGE_MODEL", "flux_2_klein_9b_q6p.ckpt")
    # Kontext model for reference-conditioned continuity (blank = disabled).
    kontext_model: str = _env("KONTEXT_MODEL", "")
    negative_prompt: str = _env(
        "NEGATIVE_PROMPT",
        "lowres, blurry, deformed, extra limbs, extra fingers, text, watermark, "
        "signature, cartoon, 3d render, pointed ears, elf, elven, fairy, "
        "anthropomorphic, animal ears, fur, whiskers, tail",
    )
    # Approach A (validated) is pure txt2img with rich descriptions + per-cast seed.
    # These extras are opt-in: reference images (used for series/Kontext later) and
    # img2img continuity between consecutive same-location scenes.
    generate_references: bool = _env("GENERATE_REFERENCES", "0") == "1"
    continuity_img2img: bool = _env("CONTINUITY_IMG2IMG", "0") == "1"
    img2img_denoise: float = float(_env("IMG2IMG_DENOISE", "0.65"))

    # --- scene segmentation ---
    # "ollama" (local LLM) | "gemini" (hosted LLM) | "heuristic" (offline, no LLM)
    segmenter: str = _env("SEGMENTER", "ollama")
    # Ollama (local)
    ollama_url: str = _env("OLLAMA_URL", "http://127.0.0.1:11434")
    ollama_model: str = _env("OLLAMA_MODEL", "gemma4:12b")
    # Gemini (hosted) — set GEMINI_API_KEY and SEGMENTER=gemini to enable.
    # Default = gemini-2.5-flash: capable enough for the fused state pass, 10k
    # requests/day free (≈25 books/day). For newest quality use gemini-3.5-flash;
    # for unlimited daily volume use gemini-2.0-flash / gemini-2.5-flash-lite.
    gemini_api_key: str = _env("GEMINI_API_KEY", "")
    gemini_model: str = _env("GEMINI_MODEL", "gemini-2.5-flash")
    # target scene length in characters (heuristic + LLM guidance)
    target_scene_chars: int = _env_int("TARGET_SCENE_CHARS", 1800)
    # Compose each scene prompt with the LLM so it mentions only what's in-scene
    # (avoids dumping the whole world into every image). Requires SEGMENTER=ollama.
    compose_prompts: bool = _env("COMPOSE_PROMPTS", "1") == "1"
    # Only entities appearing in at least this many scenes get a bible description
    # (need cross-scene consistency). One-offs are handled inline by the composer.
    bible_min_scenes: int = _env_int("BIBLE_MIN_SCENES", 2)
    # Bible-only harvest reads the WHOLE book via map-reduce: the text is walked in
    # chunks of this many characters (map: extract stable facts), then merged per
    # entity (reduce). Keep <= the LLM's context: ~24k chars (~6k tokens) is safe
    # for 32k-context local models and Gemini alike; raise it on Gemini to cut calls.
    enrich_chunk_chars: int = _env_int("ENRICH_CHUNK_CHARS", 24000)
    # Forward state pass mode (full runs): how the per-scene analysis+compose runs.
    #   "fuse"      — ONE LLM call per scene does BOTH (scene line + state deltas);
    #                 fewest calls, best for capable hosted models (Gemini).
    #   "per_scene" — TWO calls per scene (state extraction, then compose); each call
    #                 has a simpler job, better for weaker local models.
    # Blank = auto: fuse on Gemini, per_scene on Ollama.
    _state_mode_raw: str = _env("STATE_MODE", "")

    @property
    def use_llm(self) -> bool:
        """True when an LLM backend is active (ollama or gemini, not heuristic)."""
        return self.segmenter in ("ollama", "gemini")

    @property
    def state_mode(self) -> str:
        """Resolved forward-pass mode: 'fuse' or 'per_scene'. Honors STATE_MODE if
        set, else defaults to 'fuse' on Gemini and 'per_scene' on Ollama."""
        if self._state_mode_raw in ("fuse", "per_scene"):
            return self._state_mode_raw
        return "fuse" if self.segmenter == "gemini" else "per_scene"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "engine.db"

    @property
    def books_dir(self) -> Path:
        return self.data_dir / "books"

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.books_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()
