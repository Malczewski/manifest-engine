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
    """A character or location in the story bible."""

    id: str
    kind: str  # "character" | "location"
    name: str
    descriptor: str = ""
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
