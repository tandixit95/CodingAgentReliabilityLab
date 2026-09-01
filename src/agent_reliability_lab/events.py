from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class EventKind(StrEnum):
    OUTPUT = "output"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class Event:
    """One immutable runtime event emitted by an agent turn."""

    sequence: int
    run_id: str
    turn_id: str
    kind: EventKind
    payload: str = ""
