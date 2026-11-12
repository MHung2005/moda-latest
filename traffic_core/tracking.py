"""Typed in-memory state for tracked vehicles."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterator
import uuid


@dataclass(frozen=True, order=True)
class TrackKey:
    source_id: int
    object_id: int


@dataclass
class TrackState:
    key: TrackKey
    section_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    reentry_armed: bool = False
    outside_since_capture: bool = False
    captured_at: str | None = None
    plate_values: list[str] = field(default_factory=list)
    plate_confidences: list[list[float]] = field(default_factory=list)
    capture_image: Any = None
    collection_started_at: float | None = None
    last_seen_at: float | None = None
    active_reentry_id: int | None = None
    parent_key: TrackKey | None = None
    inside_at_frame: int | None = None
    capture_frame: int | None = None
    raw_plate: str = ""

    @property
    def has_capture(self) -> bool:
        return self.capture_frame is not None

    def add_plate(self, value: str, confidences: list[float]) -> None:
        self.plate_values.append(value)
        self.plate_confidences.append(list(confidences))

    def update_raw_plate(self, value: str) -> None:
        if len(value) >= len(self.raw_plate):
            self.raw_plate = value

    def observe(self, observed_at: float) -> None:
        self.last_seen_at = observed_at

    def begin_reentry(self) -> bool:
        """Consume one exit/re-entry transition so it cannot trigger repeatedly."""
        if not self.outside_since_capture or not self.reentry_armed:
            return False
        self.outside_since_capture = False
        self.reentry_armed = False
        return True

    def record_capture(
        self,
        *,
        image: Any,
        frame_number: int,
        timestamp: str,
        started_at: float,
    ) -> None:
        self.capture_image = image
        self.capture_frame = frame_number
        self.captured_at = timestamp
        self.collection_started_at = started_at

    def observe_zone(
        self,
        *,
        is_inside: bool,
        frame_number: int,
        exit_frame_gap: int,
    ) -> bool:
        """Update zone state and return whether a re-entry was consumed."""
        if is_inside:
            self.inside_at_frame = frame_number
            return self.begin_reentry()
        if (
            self.inside_at_frame is not None
            and frame_number - self.inside_at_frame > exit_frame_gap
        ):
            self.outside_since_capture = True
        return False


class TrackingRegistry:
    def __init__(self):
        self._tracks: dict[TrackKey, TrackState] = {}

    def ensure(self, key: TrackKey) -> TrackState:
        state = self._tracks.get(key)
        if state is None:
            state = TrackState(key=key)
            self._tracks[key] = state
        return state

    def get(self, key: TrackKey) -> TrackState | None:
        return self._tracks.get(key)

    def remove(self, key: TrackKey) -> TrackState | None:
        return self._tracks.pop(key, None)

    def __contains__(self, key: TrackKey) -> bool:
        return key in self._tracks

    def __iter__(self) -> Iterator[TrackKey]:
        return iter(self._tracks)

    def __len__(self) -> int:
        return len(self._tracks)
