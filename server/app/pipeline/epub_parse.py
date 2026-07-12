"""Stage 1 — EPUB -> ordered chapters with stable character offsets.

The concatenation of every chapter's `text` (in order) forms the whole-book
text; each chapter's [start_offset, end_offset) indexes into that string. These
offsets are the coordinate system every later stage references, so they must be
stable and contiguous.
"""

from __future__ import annotations

import re
from pathlib import Path

import ebooklib
from bs4 import BeautifulSoup
from ebooklib import epub

from ..models import Chapter

_WS = re.compile(r"[ \t\r\f\v]+")
_BLANKLINES = re.compile(r"\n{3,}")


def _html_to_text(html: bytes) -> str:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style"]):
        tag.decompose()
    # Block-level tags become paragraph breaks so scene boundaries are sane.
    text = soup.get_text("\n")
    text = _WS.sub(" ", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    text = _BLANKLINES.sub("\n\n", text)
    return text.strip()


def _clean_title(raw: str, fallback: str) -> str:
    title = (raw or "").strip()
    return title if title else fallback


def parse_epub(path: str | Path) -> tuple[str, str, list[Chapter]]:
    """Return (title, author, sections) in reading order.

    Every spine document above a tiny threshold becomes a section; offsets are
    NOT final yet (filled by assign_offsets after story-section filtering). This
    lets the caller drop front/back matter and previews *before* the token
    coordinate system is fixed.
    """
    book = epub.read_epub(str(path))

    title = ""
    author = ""
    if book.get_metadata("DC", "title"):
        title = book.get_metadata("DC", "title")[0][0]
    if book.get_metadata("DC", "creator"):
        author = book.get_metadata("DC", "creator")[0][0]

    sections: list[Chapter] = []
    for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
        text = _html_to_text(item.get_content())
        if len(text) < 200:  # skip covers / nav / tiny fragments
            continue
        heading = text.split("\n", 1)[0][:80]
        sections.append(
            Chapter(
                idx=len(sections),
                title=_clean_title(heading, f"Section {len(sections) + 1}"),
                text=text,
                start_offset=0,
                end_offset=0,
            )
        )

    return _clean_title(title, "Untitled"), (author or "Unknown"), sections


def assign_offsets(chapters: list[Chapter]) -> list[Chapter]:
    """Assign contiguous idx + [start_offset, end_offset) over the final section
    set, consistent with whole_text(). Call after filtering to story sections."""
    offset = 0
    for i, ch in enumerate(chapters):
        ch.idx = i
        ch.start_offset = offset
        ch.end_offset = offset + len(ch.text)
        offset = ch.end_offset + 2  # +2 for the "\n\n" joiner in whole_text
    return chapters


def whole_text(chapters: list[Chapter]) -> str:
    """Reconstruct the whole-book text consistent with chapter offsets."""
    return "\n\n".join(ch.text for ch in chapters)
