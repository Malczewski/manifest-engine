"""Tolerant JSON parsing for LLM output.

Some models wrap JSON in ``` fences or add stray prose even with format=json.
This strips that and, as a last resort, extracts the outermost {...} object.
"""

from __future__ import annotations

import json
import re


def loads(text: str) -> dict:
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z0-9]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start : end + 1])
        raise
