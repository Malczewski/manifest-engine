"""LLM backend dispatcher: Ollama (local) or Gemini (hosted).

Controlled by settings.segmenter:
  "ollama"  -> local Ollama server
  "gemini"  -> Google Gemini REST API (no extra SDK needed)
  anything else -> falls through to Ollama

All callers receive a plain dict; JSON parsing and error handling are centralised
here so individual pipeline stages don't need to know which backend is active.
"""

from __future__ import annotations

import time

import httpx

from . import jsonutil
from ..config import settings


def call_json(prompt: str, schema: dict | None = None, temperature: float = 0.3) -> dict:
    """Send a prompt to the active LLM backend; return parsed JSON dict.

    Raises on network / API errors — callers are responsible for try/except.
    """
    if settings.segmenter == "gemini":
        return _gemini(prompt, schema, temperature)
    return _ollama(prompt, schema, temperature)


# ---------------------------------------------------------------------------
# Ollama
# ---------------------------------------------------------------------------

def _ollama(prompt: str, schema: dict | None, temperature: float) -> dict:
    resp = httpx.post(
        f"{settings.ollama_url}/api/generate",
        trust_env=False,  # never route localhost through a proxy
        json={
            "model": settings.ollama_model,
            "prompt": prompt,
            "stream": False,
            "format": schema or "json",
            # skip <think> for qwen3 and similar reasoning models
            "think": False,
            "options": {"temperature": temperature},
        },
        timeout=300,
    )
    resp.raise_for_status()
    return jsonutil.loads(resp.json()["response"])


# ---------------------------------------------------------------------------
# Gemini
# ---------------------------------------------------------------------------

def _to_gemini_schema(schema: dict) -> dict:
    """Recursively convert JSON Schema lowercase type names to Gemini uppercase."""
    out: dict = {}
    for k, v in schema.items():
        if k == "type" and isinstance(v, str):
            out[k] = v.upper()
        elif isinstance(v, dict):
            out[k] = _to_gemini_schema(v)
        elif isinstance(v, list):
            out[k] = [_to_gemini_schema(i) if isinstance(i, dict) else i for i in v]
        else:
            out[k] = v
    return out


def _gemini(prompt: str, schema: dict | None, temperature: float) -> dict:
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{settings.gemini_model}:generateContent"
    )
    gen_config: dict = {
        "temperature": temperature,
        "responseMimeType": "application/json",
    }
    if schema:
        gen_config["responseSchema"] = _to_gemini_schema(schema)

    # Retry on quota exhaustion (429) with exponential backoff.
    # Free tier is 15 RPM for flash models; a long book will hit this.
    for attempt in range(4):
        resp = httpx.post(
            url,
            params={"key": settings.gemini_api_key},
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": gen_config,
            },
            timeout=120,
        )
        if resp.status_code == 429 and attempt < 3:
            time.sleep(10 * (2 ** attempt))  # 10 s, 20 s, 40 s
            continue
        resp.raise_for_status()
        break

    return jsonutil.loads(resp.json()["candidates"][0]["content"]["parts"][0]["text"])
