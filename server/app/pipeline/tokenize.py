"""Stage 7a — normalized token stream + trigram index for on-device matching.

The Android matcher aligns a noisy ASR transcript against this token stream.
Normalization mirrors what ASR tends to produce: lowercase, punctuation
stripped, digits spelled out (Whisper says "twenty three", the book prints
"23"). Each token keeps its original char offset so a matched token position
maps straight back to a scene.
"""

from __future__ import annotations

import re

_TOKEN_RE = re.compile(r"[a-z0-9']+")

_ONES = [
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight",
    "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen",
    "sixteen", "seventeen", "eighteen", "nineteen",
]
_TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy",
         "eighty", "ninety"]


def _int_to_words(n: int) -> list[str]:
    """Spell small non-negative integers (0..9999); larger fall back to digits."""
    if n < 20:
        return [_ONES[n]]
    if n < 100:
        w = [_TENS[n // 10]]
        if n % 10:
            w.append(_ONES[n % 10])
        return w
    if n < 1000:
        w = [_ONES[n // 100], "hundred"]
        if n % 100:
            w += _int_to_words(n % 100)
        return w
    if n < 10000:
        w = _int_to_words(n // 1000) + ["thousand"]
        if n % 1000:
            w += _int_to_words(n % 1000)
        return w
    return [str(n)]


def tokenize(text: str) -> tuple[list[str], list[int]]:
    """Return (tokens, offsets) where offsets[i] is the char offset in `text`
    of the raw match that produced tokens[i] (digit expansions share an offset)."""
    tokens: list[str] = []
    offsets: list[int] = []
    for m in _TOKEN_RE.finditer(text.lower()):
        raw = m.group(0)
        start = m.start()
        if raw.isdigit():
            for w in _int_to_words(int(raw)):
                tokens.append(w)
                offsets.append(start)
        else:
            tokens.append(raw)
            offsets.append(start)
    return tokens, offsets


def build_trigrams(tokens: list[str]) -> list[tuple[str, int]]:
    """Word-trigram -> start position pairs for the inverted index."""
    grams: list[tuple[str, int]] = []
    for i in range(len(tokens) - 2):
        grams.append((f"{tokens[i]} {tokens[i + 1]} {tokens[i + 2]}", i))
    return grams


def token_span_for_offsets(
    offsets: list[int], start_offset: int, end_offset: int
) -> tuple[int, int]:
    """Map a [start_offset, end_offset) char range to a [start_token, end_token)
    token range using binary search over the (sorted) offsets list."""
    import bisect

    start_tok = bisect.bisect_left(offsets, start_offset)
    end_tok = bisect.bisect_left(offsets, end_offset)
    return start_tok, end_tok
