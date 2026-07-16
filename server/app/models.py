"""Data contract shared across the pipeline and the API.

`Chapter`, `Scene`, `Entity` are the in-memory representations produced by the
pipeline; they map 1:1 onto the tables written into a `.bookpack` (see
bookpack.py). API request/response shapes use the pydantic models.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from pydantic import BaseModel


class JobStatus(str, Enum):
    queued = "queued"
    running = "running"
    paused = "paused"
    done = "done"
    error = "error"


# ---------------------------------------------------------------------------
# Pipeline domain objects (also the .bookpack row shapes)
# ---------------------------------------------------------------------------


@dataclass
class Chapter:
    idx: int
    title: str
    text: str
    # char offsets into the whole-book concatenated text
    start_offset: int
    end_offset: int
    # token positions into the normalized token stream (filled during indexing)
    start_token: int = 0
    end_token: int = 0


@dataclass
class Entity:
    """A character or location in the story bible.

    `descriptor` is the STABLE identity anchor (synthesized from `facts`); it drives
    the per-cast seed and the base look, so it stays constant across the whole book.
    `facts` are the raw accumulated stable observations (hair, species, marks) that
    the forward state pass gathers scene by scene and that seed later books in a
    series. Scene-specific state (current outfit, injury) is NOT here — it lives per
    scene in Scene.overlays.
    """

    id: str
    kind: str  # "character" | "location"
    name: str
    descriptor: str = ""
    facts: list[str] = field(default_factory=list)
    image_path: str = ""  # relative path inside the pack, e.g. images/char_x.png


@dataclass
class Scene:
    id: int
    chapter_idx: int
    seq: int  # order within the chapter
    start_offset: int
    end_offset: int
    summary: str = ""
    location_id: str = ""
    characters: list[str] = field(default_factory=list)
    mood: str = ""
    time_of_day: str = ""
    key_action: str = ""
    image_path: str = ""  # relative path inside the pack, e.g. images/scene_0001.png
    # Final composed image prompt for this scene (built by the forward state pass:
    # base style + scene line + per-entity identity + per-entity temporal overlay).
    prompt: str = ""
    # Per-entity scene-specific visible state in effect at this scene (name ->
    # "wearing a blue cloak" / "left arm in a sling"). Carried forward until changed.
    overlays: dict[str, str] = field(default_factory=dict)
    # token positions, filled during indexing
    start_token: int = 0
    end_token: int = 0


# ---------------------------------------------------------------------------
# API shapes
# ---------------------------------------------------------------------------


class BookSummary(BaseModel):
    id: str
    title: str
    author: str
    status: JobStatus
    num_scenes: int = 0
    has_pack: bool = False
    series_id: str = ""
    series_seq: int = 0


class SeriesInfo(BaseModel):
    id: str
    name: str
    book_count: int = 0


class JobInfo(BaseModel):
    id: str
    book_id: str
    status: JobStatus
    stage: str = ""
    progress: float = 0.0  # 0..1
    message: str = ""
    error: Optional[str] = None
