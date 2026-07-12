"""Stage 1b — keep only the MAIN story sections.

Real EPUBs carry table-of-contents, copyright, dedications, "about the author",
ads, and — critically — full multi-thousand-word EXCERPTS of *other* books, often
titled "Chapter 1". Those must not be segmented or illustrated.

Length and title don't separate a preview from a real chapter, but *position*
does: a book is front-matter, then the story, then back-matter/previews that begin
at a marker like "If you enjoyed …" or "An excerpt from …". So we skip leading
front-matter and keep sections until the first back-matter marker after the story
has started. This positional rule is robust and needs no LLM.
"""

from __future__ import annotations

import re

from ..models import Chapter

# Matched against a section's opening text (lowercased).
_FRONT_MATTER = [
    r"^contents\b",
    r"^table of contents",
    r"work of fiction",
    r"all rights reserved",
    r"this book is a work",
    r"copyright ",
    r"^by [a-z .'-]+$",
    r"^dedication\b",
    r"^title page",
]

# A section starting with one of these ENDS the story (back matter / previews).
_BACK_MATTER_START = [
    r"if you enjoyed",
    r"about the author",
    r"also by\b",
    r"an excerpt",
    r"excerpt from",
    r"read on for",
    r"continue reading",
    r"keep reading",
    r"newsletter",
    r"sign up",
    r"acknowledg",
]


def _matches(text: str, patterns: list[str]) -> bool:
    head = text[:400].lower().strip()
    return any(re.search(p, head, re.MULTILINE) for p in patterns)


def select_story_sections(sections: list[Chapter]) -> list[Chapter]:
    """Return only the main-story sections, in reading order."""
    if len(sections) <= 1:
        return sections

    story: list[Chapter] = []
    started = False
    for s in sections:
        if _matches(s.text, _BACK_MATTER_START):
            if started:
                break  # story is over; everything after is back matter/previews
            continue  # a back-matter-ish blurb before the story starts: skip
        if not started and _matches(s.text, _FRONT_MATTER):
            continue  # leading front matter
        story.append(s)
        started = True

    if not story:  # nothing matched cleanly -> fall back to the largest section
        story = [max(sections, key=lambda s: len(s.text))]
    return story
